"""
Relayed DEX Stress Test: 500 RelayedV3 swapTokensFixedInput calls

Même logique que stress_dex_swap.py mais chaque swap est une transaction RelayedV3 :
le wallet signe la tx DEX, le relayer de son shard paie le gas.

Supporte 1, 2 ou 3 shards simultanément :
  - Shard-1 wallets → DEX pool (shard 1) = intra-shard
  - Shard-0/2 wallets → DEX pool (shard 1) = cross-shard

Pool:     erd1qqqqqqqqqqqqqpgqeel2kumf0r8ffyhth7pqdujjat9nx0862jpsg2pqaq
Function: swapTokensFixedInput

Usage (tous les shards) :
  python stress_dex_swap_relayed.py \\
    --shard-dir-0 ./wallets/shard-0 --relayer-0 ./wallets/relayer0.pem \\
    --shard-dir-1 ./wallets/shard-1 --relayer-1 ./wallets/relayer1.pem \\
    --shard-dir-2 ./wallets/shard-2 --relayer-2 ./wallets/relayer2.pem \\
    --token-in WEGLD-bd4d79 --amount-in 0.001 --token-out USDC-c76f1f \\
    --target 500


Usage (un seul shard) :
  python stress_dex_swap_relayed.py \\
    --shard-dir-1 ./wallets/shard-1 --relayer-1 ./wallets/relayer1.pem \\
    --token-in WEGLD-bd4d79 --amount-in 0.001 --token-out USDC-c76f1f \\
    --target 500

Notes:
  - Chaque wallet doit détenir assez de --token-in (target/nb_wallets * amount-in)
  - Chaque relayer doit avoir de l'EGLD pour le gas (~6 EGLD pour 500 txs totales)
  - --max-wallets s'applique par shard


  python stress_dex_swap_relayed.py \
  --shard-dir-0 ./wallets/shard-0 --relayer-0 ./wallets/relayer0.pem \
  --shard-dir-1 ./wallets/shard-1 --relayer-1 ./wallets/relayer1.pem \
  --shard-dir-2 ./wallets/shard-2 --relayer-2 ./wallets/relayer2.pem \
  --token-in WEGLD-bd4d79 --amount-in 0.001 --token-out USDC-c76f1f \
  --min-out 1 \
  --max-wallets 30 --target 500 \
  --dry-run
"""
import argparse
import sys
import threading
import time
from pathlib import Path

from multiversx_sdk import Address, Transaction, TransactionComputer

import config
import utils

EGLD_DENOMINATION = 10 ** 18
DEX_POOL = "erd1qqqqqqqqqqqqqpgqeel2kumf0r8ffyhth7pqdujjat9nx0862jpsg2pqaq"
DEFAULT_GAS_SWAP = 15_000_000
GAS_RELAYER_OVERHEAD = 50_000
MAX_NONCE_LOOKAHEAD = 95
MAX_CONCURRENT_API_CALLS = 20


def get_nonce_safe(provider, address, api_semaphore: threading.Semaphore) -> int:
    with api_semaphore:
        return utils.get_account_nonce(provider, address)


def encode_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        return value.encode().hex()
    return value.hex()


def encode_amount_hex(amount: int) -> str:
    if amount == 0:
        return "00"
    h = hex(amount)[2:]
    return h if len(h) % 2 == 0 else "0" + h


def build_swap_data(token_in: str, amount_in_raw: int, token_out: str, min_out: int) -> str:
    return "@".join([
        "ESDTTransfer",
        encode_hex(token_in),
        encode_amount_hex(amount_in_raw),
        encode_hex("swapTokensFixedInput"),
        encode_hex(token_out),
        encode_amount_hex(min_out),
    ])


def load_wallets(wallets_dir: str, max_wallets: int) -> list:
    pem_files = sorted(Path(wallets_dir).expanduser().glob("*.pem"))[:max_wallets]
    if not pem_files:
        print(f"[ERROR] No .pem files found in: {wallets_dir}", file=sys.stderr)
        sys.exit(1)
    return [str(p) for p in pem_files]


def wallet_worker(
    wallet_pem: str,
    txs_to_send: int,
    data_str: str,
    gas_limit: int,
    relayer_signer,
    relayer_address,
    provider,
    dry_run: bool,
    counter: list,
    lock: threading.Lock,
    api_semaphore: threading.Semaphore,
):
    signer = utils.load_signer(wallet_pem)
    sender = utils.get_address(signer)
    computer = TransactionComputer()

    try:
        nonce = get_nonce_safe(provider, sender, api_semaphore)
    except Exception as e:
        print(f"[ERROR] Nonce fetch failed for {sender.to_bech32()[:12]}...: {e}")
        return

    #print(f"[INFO] {sender.to_bech32()[:16]}... nonce={nonce}, txs={txs_to_send}")
    confirmed_nonce = nonce

    for _ in range(txs_to_send):
        while not dry_run and nonce - confirmed_nonce >= MAX_NONCE_LOOKAHEAD:
            time.sleep(0.5)
            try:
                confirmed_nonce = get_nonce_safe(provider, sender, api_semaphore)
            except Exception:
                pass

        tx = Transaction(
            nonce=nonce,
            sender=sender,
            receiver=Address.new_from_bech32(DEX_POOL),
            relayer=relayer_address,
            value=0,
            gas_limit=gas_limit,
            data=data_str.encode(),
            chain_id=config.CHAIN_ID,
            gas_price=config.DEFAULT_GAS_PRICE,
            version=config.DEFAULT_TX_VERSION,
        )
        tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
        tx.relayer_signature = relayer_signer.sign(computer.compute_bytes_for_signing(tx))
        nonce += 1

        if not dry_run:
            try:
                provider.send_transaction(tx)
            except Exception as e:
                print(f"[WARN] {sender.to_bech32()[:12]}... nonce={nonce - 1}: {e}")

        with lock:
            counter[0] += 1
            total = counter[0]
        if total % 100 == 0:
            tag = "[DRY-RUN]" if dry_run else "[INFO]"
            print(f"{tag} {total} relayed swap txs sent")


def main():
    parser = argparse.ArgumentParser(description="Relayed DEX Stress Test — 500 RelayedV3 swaps")
    # Shards (tous optionnels, au moins un requis)
    parser.add_argument("--shard-dir-0", default=None, help="Dossier wallets shard-0")
    parser.add_argument("--relayer-0", default=None, help="Relayer shard-0 PEM")
    parser.add_argument("--shard-dir-1", default=None, help="Dossier wallets shard-1")
    parser.add_argument("--relayer-1", default=None, help="Relayer shard-1 PEM")
    parser.add_argument("--shard-dir-2", default=None, help="Dossier wallets shard-2")
    parser.add_argument("--relayer-2", default=None, help="Relayer shard-2 PEM")
    parser.add_argument("--max-wallets", type=int, default=100,
                        help="Max wallets par shard (défaut: 100)")
    # DEX params
    parser.add_argument("--token-in", required=True, help="Token d'entrée (ex: WEGLD-bd4d79)")
    parser.add_argument("--amount-in", type=float, required=True,
                        help="Montant par swap (unités humaines)")
    parser.add_argument("--token-out", required=True, help="Token de sortie (ex: USDC-c76f1f)")
    parser.add_argument("--min-out", type=int, default=1,
                        help="Montant minimum de sortie en raw (défaut: 1)")
    parser.add_argument("--decimals", type=int, default=18,
                        help="Décimales du token d'entrée (défaut: 18)")
    parser.add_argument("--target", type=int, default=500,
                        help="Nombre total de swaps (défaut: 500)")
    parser.add_argument("--gas", type=int, default=DEFAULT_GAS_SWAP + GAS_RELAYER_OVERHEAD,
                        help=f"Gas limit par swap (défaut: {DEFAULT_GAS_SWAP + GAS_RELAYER_OVERHEAD:,})")
    parser.add_argument("--dry-run", action="store_true", help="Construire/signer sans envoyer")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    # Valider : au moins un shard configuré, et dir+relayer toujours par paire
    shard_configs = [
        (0, args.shard_dir_0, args.relayer_0),
        (1, args.shard_dir_1, args.relayer_1),
        (2, args.shard_dir_2, args.relayer_2),
    ]
    active_shards = []
    for sid, shard_dir, relayer_pem in shard_configs:
        if shard_dir and relayer_pem:
            active_shards.append((sid, shard_dir, relayer_pem))
        elif shard_dir or relayer_pem:
            print(f"[ERROR] --shard-dir-{sid} et --relayer-{sid} doivent être fournis ensemble",
                  file=sys.stderr)
            sys.exit(1)

    if not active_shards:
        print("[ERROR] Fournir au moins un couple --shard-dir-X + --relayer-X", file=sys.stderr)
        sys.exit(1)

    provider = utils.get_provider(args.gateway)
    amount_in_raw = int(args.amount_in * (10 ** args.decimals))
    data_str = build_swap_data(args.token_in, amount_in_raw, args.token_out, args.min_out)

    # Charger wallets + relayers par shard actif
    all_wallets = []  # [(pem_path, relayer_signer, relayer_address, shard_id)]
    for sid, shard_dir, relayer_pem in active_shards:
        wallets = load_wallets(shard_dir, args.max_wallets)
        rel_signer = utils.load_signer(relayer_pem)
        rel_address = utils.get_address(rel_signer)
        shard_type = "intra" if sid == 1 else "cross"
        print(f"[INFO] Shard-{sid} ({shard_type}-shard): {len(wallets)} wallets | relayer={rel_address.to_bech32()[:16]}...")
        for pem in wallets:
            all_wallets.append((pem, rel_signer, rel_address, sid))

    # Distribuer les swaps équitablement
    n = len(all_wallets)
    base, extra = divmod(args.target, n)
    txs_per_wallet = [base + (1 if i < extra else 0) for i in range(n)]
    gas_cost_egld = args.target * args.gas * config.DEFAULT_GAS_PRICE * 1e-18

    print(f"\n[INFO] Pool:      {DEX_POOL}")
    print(f"[INFO] Token In:  {args.token_in} | {args.amount_in} units = {amount_in_raw} raw")
    print(f"[INFO] Token Out: {args.token_out} | min_out={args.min_out}")
    print(f"[INFO] Wallets:   {n} total | Target: {args.target} swaps | Gas: {args.gas:,}")
    print(f"[INFO] Gas total: ~{gas_cost_egld:.2f} EGLD (split between relayers)")
    if args.dry_run:
        print("[INFO] DRY-RUN – transactions will NOT be sent\n")

    counter = [0]
    lock = threading.Lock()
    api_semaphore = threading.Semaphore(MAX_CONCURRENT_API_CALLS)
    start = time.time()

    threads = [
        threading.Thread(
            target=wallet_worker,
            args=(pem, txs, data_str, args.gas, rel_signer, rel_addr,
                  provider, args.dry_run, counter, lock, api_semaphore),
            daemon=True,
        )
        for (pem, rel_signer, rel_addr, _sid), txs in zip(all_wallets, txs_per_wallet)
        if txs > 0
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    elapsed = time.time() - start
    total = counter[0]
    rate = total / elapsed if elapsed > 0 else 0
    print(f"\n[DONE] {total} relayed swap txs in {elapsed:.1f}s ({rate:.1f} tx/s)")


if __name__ == "__main__":
    main()
