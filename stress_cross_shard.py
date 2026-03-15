"""
Cross-Shard Stress Test: 50,000 MoveBalance transactions across shards.

Each wallet sends to the next shard in a round-robin rotation:
  shard-0 → shard-1 → shard-2 → shard-0

Receiver cycling (same formula as stress_move_balance.py):
  shard-0[i] cycles through all shard-1 wallets
  shard-1[i] cycles through all shard-2 wallets
  shard-2[i] cycles through all shard-0 wallets

Usage:
  python stress_cross_shard.py \\
    --shard-dir-0 ./wallets/shard-0 \\
    --shard-dir-1 ./wallets/shard-1 \\
    --shard-dir-2 ./wallets/shard-2 \\
    --target 10000

  # Dry-run
  python stress_cross_shard.py \\
    --shard-dir-0 ./wallets/shard-0 \\
    --shard-dir-1 ./wallets/shard-1 \\
    --shard-dir-2 ./wallets/shard-2 \\
    --target 10000 --dry-run

    python stress_cross_shard.py \
    --shard-dir-0 ./wallets/shard-0 \
    --shard-dir-1 ./wallets/shard-1 \
    --shard-dir-2 ./wallets/shard-2 \
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
GAS_MOVE_BALANCE = 50_000
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
        # Throttle if nonce is too far ahead of confirmed
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
            value=value_raw,
            gas_limit=GAS_MOVE_BALANCE,
            data=b"",
            chain_id=config.CHAIN_ID,
            gas_price=config.DEFAULT_GAS_PRICE,
            version=config.DEFAULT_TX_VERSION,
        )
        tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
        nonce += 1

        if not dry_run:
            try:
                provider.send_transaction(tx)
            except Exception as e:
                print(f"[WARN] {sender['name']} nonce={nonce - 1}: {e}")

        with lock:
            counter[0] += 1
            total = counter[0]
        if total % 1000 == 0:
            tag = "[DRY-RUN]" if dry_run else "[INFO]"
            print(f"{tag} {total:,} transactions sent")

        if delay > 0:
            time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(description="Cross-Shard Stress Test — 50k MoveBalance")
    parser.add_argument("--shard-dir-0", required=True, help="Dossier wallets shard-0")
    parser.add_argument("--shard-dir-1", required=True, help="Dossier wallets shard-1")
    parser.add_argument("--shard-dir-2", required=True, help="Dossier wallets shard-2")
    parser.add_argument("--max-wallets", type=int, default=100,
                        help="Max wallets par shard (défaut: 100)")
    parser.add_argument("--target", type=int, default=50_000,
                        help="Total transactions à envoyer (défaut: 50000)")
    parser.add_argument("--value", type=float, default=0.0,
                        help="EGLD par tx (défaut: 0.0)")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="Délai entre txs par wallet en secondes (défaut: 0)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Construire/signer sans envoyer")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    # Charger les wallets de chaque shard
    shards = [
        load_wallets(args.shard_dir_0, args.max_wallets),
        load_wallets(args.shard_dir_1, args.max_wallets),
        load_wallets(args.shard_dir_2, args.max_wallets),
    ]

    for i, wallets in enumerate(shards):
        print(f"[INFO] Shard-{i}: {len(wallets)} wallets")

    # Vérification : chaque shard doit avoir au moins 1 wallet
    for i, wallets in enumerate(shards):
        if not wallets:
            print(f"[ERROR] Shard-{i} has no wallets", file=sys.stderr)
            sys.exit(1)

    # Distribuer les txs sur tous les wallets (3 shards)
    all_senders = [(w, (i + 1) % 3) for i, shard in enumerate(shards) for w in shard]
    total_senders = len(all_senders)
    base, extra = divmod(args.target, total_senders)
    txs_per_sender = [base + (1 if i < extra else 0) for i in range(total_senders)]

    value_raw = int(args.value * EGLD_DENOMINATION)
    print(f"\n[INFO] Total wallets: {total_senders} | Target: {args.target:,} txs")
    print(f"[INFO] ~{base}-{base + 1} txs/wallet | value={args.value} EGLD | gas={GAS_MOVE_BALANCE:,}")
    print(f"[INFO] Routing: shard-0→shard-1 | shard-1→shard-2 | shard-2→shard-0")
    if args.dry_run:
        print("[INFO] DRY-RUN – transactions will NOT be sent\n")

    provider = utils.get_provider(args.gateway)
    counter = [0]
    lock = threading.Lock()
    api_semaphore = threading.Semaphore(MAX_CONCURRENT_API_CALLS)
    start = time.time()

    threads = []
    for (sender, recv_shard_idx), txs in zip(all_senders, txs_per_sender):
        if txs == 0:
            continue
        receiver_pool = shards[recv_shard_idx]
        t = threading.Thread(
            target=wallet_worker,
            args=(sender, receiver_pool, txs, value_raw, provider,
                  args.dry_run, counter, lock, args.delay, api_semaphore),
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
    print(f"\n[DONE] {total:,} cross-shard transactions in {elapsed:.1f}s ({rate:.0f} tx/s)")


if __name__ == "__main__":
    main()
