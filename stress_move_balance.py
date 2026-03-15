"""
Window A – Stress Test: 50,000 Intra-Shard MoveBalance

Loads up to 100 PEM wallets from a directory, groups them by shard,
then sends EGLD transfers between wallets in the same shard using
one thread per wallet for maximum throughput.

Usage:
  python stress_move_balance.py --wallets-dir ./wallets [options]

Examples:
  # Envoyer 50k txs depuis les wallets shard-1, en excluant le wallet principal
  python stress_move_balance.py --wallets-dir ./wallets/shard-1 \
    --exclude-wallet ./wallets/bon_supernova.pem --target 50000

  # Dry-run
  python stress_move_balance.py --wallets-dir ./wallets/shard-1 \
    --exclude-wallet ./wallets/bon_supernova.pem --dry-run
"""
import argparse
import sys
import threading
import time
from pathlib import Path

from multiversx_sdk import AddressComputer, Transaction, TransactionComputer

import config
import utils

EGLD_DENOMINATION = 10 ** 18
GAS_MOVE_BALANCE = 50_000
MAX_NONCE_LOOKAHEAD = 95    # MultiversX rejects nonce if > maxNonceDelta ahead of confirmed
MAX_CONCURRENT_API_CALLS = 20  # Cap concurrent HTTP requests to avoid overwhelming local gateway


def get_nonce_safe(provider, address, api_semaphore: threading.Semaphore) -> int:
    with api_semaphore:
        return utils.get_account_nonce(provider, address)


def get_shard_id(address, num_shards: int) -> int:
    """Compute shard ID using the official MultiversX AddressComputer (bitwise formula)."""
    return AddressComputer(number_of_shards=num_shards).get_shard_of_address(address)


def load_wallets(wallets_dir: str, max_wallets: int = 100) -> list:
    pem_files = sorted(Path(wallets_dir).expanduser().glob("*.pem"))[:max_wallets]
    if not pem_files:
        print(f"[ERROR] No .pem files found in: {wallets_dir}", file=sys.stderr)
        sys.exit(1)
    wallets = []
    for p in pem_files:
        signer = utils.load_signer(str(p))
        address = utils.get_address(signer)
        wallets.append({"signer": signer, "address": address, "name": p.name})
    print(f"[INFO] Loaded {len(wallets)} wallets from {wallets_dir}")
    return wallets


def group_by_shard(wallets: list, num_shards: int) -> dict:
    groups: dict[int, list] = {}
    for w in wallets:
        sid = get_shard_id(w["address"], num_shards)
        groups.setdefault(sid, []).append(w)
    return groups


def wallet_worker(
    idx: int,
    pool: list,
    txs_to_send: int,
    value_raw: int,
    provider,
    dry_run: bool,
    counter: list,
    lock: threading.Lock,
    delay: float,
    api_semaphore: threading.Semaphore,
):
    """
    Send txs_to_send transactions from wallet[idx] to other wallets in the pool.
    Nonce is fetched once then incremented locally for speed.
    Receivers cycle through all other wallets in the pool (never self).
    """
    w = pool[idx]
    signer = w["signer"]
    sender = w["address"]
    n = len(pool)
    computer = TransactionComputer()

    try:
        nonce = get_nonce_safe(provider, sender, api_semaphore)
    except Exception as e:
        print(f"[ERROR] Nonce fetch failed for {w['name']}: {e}")
        return

    confirmed_nonce = nonce

    for j in range(txs_to_send):
        # Pause if local nonce is too far ahead of confirmed on-chain nonce
        while not dry_run and nonce - confirmed_nonce >= MAX_NONCE_LOOKAHEAD:
            time.sleep(0.5)
            try:
                confirmed_nonce = get_nonce_safe(provider, sender, api_semaphore)
            except Exception:
                pass
        # Cycle through all n-1 receivers, never self
        # Formula proven safe: (idx + 1 + j % (n-1)) % n != idx for n >= 2
        receiver_idx = (idx + 1 + j % (n - 1)) % n
        receiver = pool[receiver_idx]["address"]

        tx = Transaction(
            nonce=nonce,
            sender=sender,
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
                print(f"[WARN] {w['name']} nonce={nonce - 1}: {e}")

        with lock:
            counter[0] += 1
            total = counter[0]
        if total % 1000 == 0:
            tag = "[DRY-RUN]" if dry_run else "[INFO]"
            print(f"{tag} {total} transactions sent")

        if delay > 0:
            time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(description="Window A – 50k Intra-Shard MoveBalance")
    parser.add_argument("--wallets-dir", required=True, help="Directory containing .pem wallet files")
    parser.add_argument("--num-shards", type=int, default=3, help="Number of shards on the network (default: 3)")
    parser.add_argument("--shard", type=int, default=None, help="Force a specific shard ID (auto if omitted)")
    parser.add_argument("--target", type=int, default=50_000, help="Total transactions to send (default: 50000)")
    parser.add_argument("--value", type=float, default=0.0, help="EGLD value per tx (default: 0.0)")
    parser.add_argument("--max-wallets", type=int, default=100, help="Max wallets to use (default: 100)")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay (seconds) between txs per wallet (default: 0)")
    parser.add_argument("--exclude-wallet", default=None,
                        help="Exclure un wallet PEM du pool (ex: wallet principal)")
    parser.add_argument("--dry-run", action="store_true", help="Build and sign txs but don't send")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    provider = utils.get_provider(args.gateway)
    wallets = load_wallets(args.wallets_dir, args.max_wallets)

    # Exclure le wallet principal s'il se retrouve dans le dossier
    if args.exclude_wallet:
        excl_signer = utils.load_signer(args.exclude_wallet)
        excl_addr = utils.get_address(excl_signer).to_bech32()
        before = len(wallets)
        wallets = [w for w in wallets if w["address"].to_bech32() != excl_addr]
        if len(wallets) < before:
            print(f"[INFO] Excluded main wallet: {excl_addr}")

    # Group by shard and report
    groups = group_by_shard(wallets, args.num_shards)
    for sid, wlist in sorted(groups.items()):
        print(f"[INFO] Shard {sid}: {len(wlist)} wallets")

    # Select shard
    if args.shard is not None:
        selected_shard = args.shard
    else:
        selected_shard = max(groups, key=lambda s: len(groups[s]))
        print(f"[INFO] Auto-selected shard {selected_shard} ({len(groups[selected_shard])} wallets)")

    pool = groups.get(selected_shard, [])
    if len(pool) < 2:
        print(f"[ERROR] Need >= 2 wallets in shard {selected_shard} (got {len(pool)})", file=sys.stderr)
        sys.exit(1)

    # Distribute txs evenly across wallets
    n = len(pool)
    base, extra = divmod(args.target, n)
    txs_per_wallet = [base + (1 if i < extra else 0) for i in range(n)]

    value_raw = int(args.value * EGLD_DENOMINATION)
    print(f"\n[INFO] Shard {selected_shard}: {n} wallets, {args.target} txs target")
    print(f"[INFO] ~{base}-{base + 1} txs/wallet | value={args.value} EGLD | gas={GAS_MOVE_BALANCE:,}")
    if args.dry_run:
        print("[INFO] DRY-RUN – transactions will NOT be sent\n")

    counter = [0]
    lock = threading.Lock()
    api_semaphore = threading.Semaphore(MAX_CONCURRENT_API_CALLS)
    start = time.time()

    threads = [
        threading.Thread(
            target=wallet_worker,
            args=(i, pool, txs, value_raw, provider, args.dry_run, counter, lock, args.delay, api_semaphore),
            daemon=True,
        )
        for i, txs in enumerate(txs_per_wallet)
        if txs > 0
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    elapsed = time.time() - start
    total = counter[0]
    rate = total / elapsed if elapsed > 0 else 0
    print(f"\n[DONE] {total:,} transactions in {elapsed:.1f}s ({rate:.0f} tx/s)")


if __name__ == "__main__":
    main()
