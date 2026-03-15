"""
Cross-Shard Relayed Stress Test: 10,000 RelayedV3 cross-shard MoveBalance

Même routing que stress_cross_shard.py mais chaque transaction inclut un
champ `relayer` (RelayedV3, SDK v2.4.0+). Le relayer paie le gas ;
le wallet n'a besoin que d'EGLD pour la valeur transférée (0 par défaut).

Flow par shard X :
  wallet-X (shard X)  ──▶  wallet-(X+1)%3 (shard X+1)
  relayer-X paie le gas (champ `relayer` dans la tx)

Signing :
  tx.signature         = sender_signer.sign(compute_bytes_for_signing(tx))
  tx.relayer_signature = relayer_signer.sign(compute_bytes_for_signing(tx))

Usage:
  python stress_cross_shard_relayed.py \\
    --shard-dir-0 ./wallets/shard-0 \\
    --shard-dir-1 ./wallets/shard-1 \\
    --shard-dir-2 ./wallets/shard-2 \\
    --relayer-0 ./wallets/relayer0.pem \\
    --relayer-1 ./wallets/relayer1.pem \\
    --relayer-2 ./wallets/relayer2.pem \\
    --target 10000

  # Dry-run
  python stress_cross_shard_relayed.py \
    --shard-dir-0 ./wallets/shard-0 \
    --shard-dir-1 ./wallets/shard-1 \
    --shard-dir-2 ./wallets/shard-2 \
    --relayer-0 ./wallets/relayer0.pem \
    --relayer-1 ./wallets/relayer1.pem \
    --relayer-2 ./wallets/relayer2.pem \
    --max-wallets 30 \
    --value 0.0001 \
    --target 10000 --dry-run
"""
import argparse
import sys
import threading
import time
from pathlib import Path

from multiversx_sdk import Transaction, TransactionComputer

import config
import utils

EGLD_DENOMINATION = 10 ** 18
GAS_RELAYED_MOVE_BALANCE = 100_000   # 50k inner + 50k RelayedV3 overhead
MAX_NONCE_LOOKAHEAD = 95
MAX_CONCURRENT_API_CALLS = 20


def get_nonce_safe(provider, address, api_semaphore: threading.Semaphore) -> int:
    with api_semaphore:
        return utils.get_account_nonce(provider, address)


def load_wallets(wallets_dir: str, max_wallets: int) -> list:
    pem_files = sorted(Path(wallets_dir).expanduser().glob("*.pem"))[:max_wallets]
    if not pem_files:
        print(f"[ERROR] No .pem files found in: {wallets_dir}", file=sys.stderr)
        sys.exit(1)
    wallets = []
    for p in pem_files:
        signer = utils.load_signer(str(p))
        address = utils.get_address(signer)
        wallets.append({"signer": signer, "address": address, "name": p.name})
    return wallets


def wallet_worker(
    sender: dict,
    receiver_pool: list,
    txs_to_send: int,
    relayer_signer,
    relayer_address,
    value_raw: int,
    provider,
    dry_run: bool,
    counter: list,
    lock: threading.Lock,
    delay: float,
    api_semaphore: threading.Semaphore,
):
    signer = sender["signer"]
    sender_addr = sender["address"]
    n = len(receiver_pool)
    computer = TransactionComputer()

    try:
        nonce = get_nonce_safe(provider, sender_addr, api_semaphore)
    except Exception as e:
        print(f"[ERROR] Nonce fetch failed for {sender['name']}: {e}")
        return

    confirmed_nonce = nonce

    for j in range(txs_to_send):
        # Throttle si le nonce local est trop loin du nonce confirmé
        while not dry_run and nonce - confirmed_nonce >= MAX_NONCE_LOOKAHEAD:
            time.sleep(0.5)
            try:
                confirmed_nonce = get_nonce_safe(provider, sender_addr, api_semaphore)
            except Exception:
                pass

        receiver = receiver_pool[j % n]["address"]

        tx = Transaction(
            nonce=nonce,
            sender=sender_addr,
            receiver=receiver,
            relayer=relayer_address,
            value=value_raw,
            gas_limit=GAS_RELAYED_MOVE_BALANCE,
            data=b"",
            chain_id=config.CHAIN_ID,
            gas_price=config.DEFAULT_GAS_PRICE,
            version=config.DEFAULT_TX_VERSION,
        )
        # RelayedV3 : sender signe en premier, puis relayer signe les mêmes bytes
        tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
        tx.relayer_signature = relayer_signer.sign(computer.compute_bytes_for_signing(tx))
        nonce += 1

        if not dry_run:
            try:
                provider.send_transaction(tx)
            except Exception as e:
                print(f"[WARN] {sender['name']} nonce={nonce - 1}: {e}")

        with lock:
            counter[0] += 1
            total = counter[0]
        if total % 500 == 0:
            tag = "[DRY-RUN]" if dry_run else "[INFO]"
            print(f"{tag} {total:,} relayed txs sent")

        if delay > 0:
            time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(description="Cross-Shard RelayedV3 Stress Test — 10k txs")
    parser.add_argument("--shard-dir-0", required=True, help="Dossier wallets shard-0")
    parser.add_argument("--shard-dir-1", required=True, help="Dossier wallets shard-1")
    parser.add_argument("--shard-dir-2", required=True, help="Dossier wallets shard-2")
    parser.add_argument("--relayer-0", required=True, help="Wallet relayer shard-0 PEM")
    parser.add_argument("--relayer-1", required=True, help="Wallet relayer shard-1 PEM")
    parser.add_argument("--relayer-2", required=True, help="Wallet relayer shard-2 PEM")
    parser.add_argument("--max-wallets", type=int, default=100,
                        help="Max wallets par shard (défaut: 100)")
    parser.add_argument("--target", type=int, default=10_000,
                        help="Total transactions à envoyer (défaut: 10000)")
    parser.add_argument("--value", type=float, default=0.0,
                        help="EGLD par tx (défaut: 0.0)")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="Délai entre txs par wallet en secondes (défaut: 0)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Construire/signer sans envoyer")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    # Charger les wallets de challenge
    shards = [
        load_wallets(args.shard_dir_0, args.max_wallets),
        load_wallets(args.shard_dir_1, args.max_wallets),
        load_wallets(args.shard_dir_2, args.max_wallets),
    ]

    # Charger les relayers (1 par shard) — UserSigner est stateless, thread-safe
    relayer_pems = [args.relayer_0, args.relayer_1, args.relayer_2]
    relayers = []
    for i, pem in enumerate(relayer_pems):
        signer = utils.load_signer(pem)
        address = utils.get_address(signer)
        relayers.append({"signer": signer, "address": address})
        print(f"[INFO] Relayer-{i}: {address.to_bech32()}")

    for i, wallets in enumerate(shards):
        print(f"[INFO] Shard-{i}: {len(wallets)} wallets")

    # Distribution des txs sur tous les wallets des 3 shards
    all_senders = [
        (wallet, shard_idx, (shard_idx + 1) % 3)
        for shard_idx, shard in enumerate(shards)
        for wallet in shard
    ]
    total_senders = len(all_senders)
    base, extra = divmod(args.target, total_senders)
    txs_per_sender = [base + (1 if i < extra else 0) for i in range(total_senders)]

    value_raw = int(args.value * EGLD_DENOMINATION)
    print(f"\n[INFO] Total wallets : {total_senders} | Target : {args.target:,} txs")
    print(f"[INFO] ~{base}-{base + 1} txs/wallet | value={args.value} EGLD | gas={GAS_RELAYED_MOVE_BALANCE:,}")
    print(f"[INFO] Routing : shard-0→shard-1 | shard-1→shard-2 | shard-2→shard-0")
    print(f"[INFO] Relayed V3 — gas paid by relayer (~{args.target * GAS_RELAYED_MOVE_BALANCE * 1e-18:.3f} EGLD/relayer)")
    if args.dry_run:
        print("[INFO] DRY-RUN – transactions will NOT be sent\n")

    provider = utils.get_provider(args.gateway)
    counter = [0]
    lock = threading.Lock()
    api_semaphore = threading.Semaphore(MAX_CONCURRENT_API_CALLS)
    start = time.time()

    threads = []
    for (sender, shard_idx, recv_shard_idx), txs in zip(all_senders, txs_per_sender):
        if txs == 0:
            continue
        t = threading.Thread(
            target=wallet_worker,
            args=(
                sender,
                shards[recv_shard_idx],
                txs,
                relayers[shard_idx]["signer"],
                relayers[shard_idx]["address"],
                value_raw,
                provider,
                args.dry_run,
                counter,
                lock,
                args.delay,
                api_semaphore,
            ),
            daemon=True,
        )
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    elapsed = time.time() - start
    total = counter[0]
    rate = total / elapsed if elapsed > 0 else 0
    print(f"\n[DONE] {total:,} relayed cross-shard txs in {elapsed:.1f}s ({rate:.0f} tx/s)")


if __name__ == "__main__":
    main()
