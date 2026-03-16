"""
Burn EGLD Stress Test: brûler le maximum d'EGLD intra-shard.

Identique à stress_burn_egld_3.py + gas price premium conditionnel à la congestion :
  - Un thread background poll la taille du pool toutes les POOL_POLL_INTERVAL secondes.
  - Si pool >= POOL_CONGESTION_THRESHOLD : les wallets riches paient PREMIUM_GAS_PRICE.
  - Sinon : tout le monde paie DEFAULT_GAS_PRICE (inutile de payer premium sans compétition).

450 wallets du même shard se passent de l'EGLD en round-robin (wallet[i] → wallet[i+1]).
Les transactions sont envoyées en bulk (provider.send_transactions) :
1 requête HTTP par batch de 90 txs.

Usage:
  python stress_burn_egld_4.py \\
    --wallets-dir ./demo-wallets/shard-0 \\
    --wallets-dir ./demo-wallets/shard-1 \\
    --wallets-dir ./demo-wallets/shard-2 \\
    --max-wallets 500 \\
    --batch-size 95 \\
    --dry-run
"""
import argparse
import sys
import threading
import time
from pathlib import Path

import requests as _requests
from multiversx_sdk import AddressComputer, Transaction, TransactionComputer

import config
import utils

GAS_MOVE_BALANCE         = 50_000
GAS_COST_RAW             = GAS_MOVE_BALANCE * config.DEFAULT_GAS_PRICE
DEFAULT_AMOUNT_PER_TX    = GAS_COST_RAW
DEFAULT_BATCH_SIZE       = 90
MAX_NONCE_LOOKAHEAD      = 95
MAX_CONCURRENT_API_CALLS = 30
RICH_THRESHOLD           = int(0.1  * 1e18)
RICH_VALUE               = int(0.01 * 1e18)
PREMIUM_GAS_PRICE        = int(config.DEFAULT_GAS_PRICE ) #* 1.2
POOL_CONGESTION_THRESHOLD = 50_000   # txs dans le pool → activer le premium
POOL_POLL_INTERVAL        = 5.0     # secondes entre deux checks
# POOL_API_URL et POOL_NUM_SHARDS définis dans config.py


def get_shard_pool_size(shard: int, gateway_url: str) -> int:
    """Retourne erd_tx_pool_load depuis le node observer du shard (LAN/tunnel SSH, O(1))."""
    try:
        port = config.NODE_BASE_PORT + shard
        r = _requests.get(f"http://{config.NODE_HOST}:{port}/node/status", timeout=3)
        if r.status_code == 200:
            metrics = r.json().get("data", {}).get("metrics", {})
            return int(metrics.get(config.NODE_POOL_FIELD, 0))
    except Exception:
        pass
    return 0


def pool_monitor(gateway_url: str, pool_congested: list, stop_event: threading.Event):
    """Thread background : met à jour pool_congested[shard] toutes les POOL_POLL_INTERVAL s."""
    ports = [config.NODE_BASE_PORT + s for s in range(config.POOL_NUM_SHARDS)]
    print(f"[POOL] source: {config.NODE_HOST} ports {ports} | champ: {config.NODE_POOL_FIELD}")

    while not stop_event.is_set():
        sizes = [None] * config.POOL_NUM_SHARDS

        def _fetch(s):
            sizes[s] = get_shard_pool_size(s, gateway_url)

        fetch_threads = [threading.Thread(target=_fetch, args=(s,)) for s in range(config.POOL_NUM_SHARDS)]
        for ft in fetch_threads:
            ft.start()
        for ft in fetch_threads:
            ft.join(timeout=6)

        parts = []
        for shard in range(config.POOL_NUM_SHARDS):
            size = sizes[shard] or 0
            pool_congested[shard] = size >= POOL_CONGESTION_THRESHOLD
            status = "!" if pool_congested[shard] else "ok"
            parts.append(f"shard-{shard}: {size:,} ({status})")
        print(f"[POOL] {' | '.join(parts)}")
        stop_event.wait(POOL_POLL_INTERVAL)


def get_account_safe(provider, address, api_semaphore) -> tuple[int, int]:
    """Retourne (nonce_confirmé, balance_raw) en un seul appel API."""
    with api_semaphore:
        acc = provider.get_account(address)
        return acc.nonce, int(acc.balance)


def load_wallets(wallets_dirs: list[str], max_wallets: int) -> list[dict]:
    all_pems = []
    for d in wallets_dirs:
        pems = sorted(Path(d).expanduser().glob("*.pem"))
        all_pems.extend(pems)
    all_pems = all_pems[:max_wallets]
    if not all_pems:
        print("[ERROR] Aucun .pem trouvé", file=sys.stderr)
        sys.exit(1)
    wallets = []
    for p in all_pems:
        signer = utils.load_signer(str(p))
        address = utils.get_address(signer)
        wallets.append({"signer": signer, "address": address, "name": p.name})
    return wallets


def wallet_worker(
    idx: int,
    all_wallets: list[dict],
    amount_per_tx: int,
    batch_size: int,
    provider,
    dry_run: bool,
    counter: list,
    lock: threading.Lock,
    api_semaphore: threading.Semaphore,
    pool_congested: list,
    stop_event: threading.Event,
):
    wallet = all_wallets[idx]
    signer = wallet["signer"]
    sender = wallet["address"]
    n = len(all_wallets)
    receiver = all_wallets[(idx + 1) % n]["address"]
    computer = TransactionComputer()
    address_computer = AddressComputer(number_of_shards=config.POOL_NUM_SHARDS)
    sender_shard = address_computer.get_shard_of_address(sender)

    try:
        confirmed_nonce, _ = get_account_safe(provider, sender, api_semaphore)
    except Exception as e:
        print(f"[ERROR] Init failed for {wallet['name']}: {e}")
        return

    local_nonce = confirmed_nonce
    empty_streak = 0

    while not stop_event.is_set():
        # 1. Balance + nonce confirmé
        try:
            confirmed_nonce, balance = get_account_safe(provider, sender, api_semaphore)
        except Exception:
            time.sleep(0.5)
            continue

        # 2. Effective value + gas price conditionnel
        effective_value = RICH_VALUE if balance >= RICH_THRESHOLD else amount_per_tx
        effective_gas_price = (
            PREMIUM_GAS_PRICE
            if balance >= RICH_THRESHOLD and pool_congested[sender_shard]
            else config.DEFAULT_GAS_PRICE
        )

        # 3. Stop si wallet épuisé (balance disponible après pending)
        pending_count = local_nonce - confirmed_nonce
        available_balance = max(0, balance - pending_count * (GAS_COST_RAW + effective_value))
        if available_balance == 0 or available_balance < GAS_COST_RAW:
            wait = min(1.0 * (2 ** empty_streak), 30.0)
            empty_streak += 1
            time.sleep(wait)
            continue

        empty_streak = 0

        # 4. Slots nonce disponibles
        nonce_slots = MAX_NONCE_LOOKAHEAD - (local_nonce - confirmed_nonce)
        if nonce_slots <= 0:
            time.sleep(0.3)
            continue

        # 5. Décision value + taille du batch
        if available_balance >= GAS_COST_RAW + effective_value:
            value = effective_value
            max_by_balance = available_balance // (GAS_COST_RAW + effective_value)
        else:
            value = 0
            max_by_balance = 1

        batch_count = min(batch_size, nonce_slots, max_by_balance)
        if batch_count == 0:
            time.sleep(0.5)
            continue

        # 6. Build + sign le batch
        batch = []
        for j in range(batch_count):
            tx = Transaction(
                nonce=local_nonce + j,
                sender=sender,
                receiver=receiver,
                value=value,
                gas_limit=GAS_MOVE_BALANCE,
                data=b"",
                chain_id=config.CHAIN_ID,
                gas_price=effective_gas_price,
                version=config.DEFAULT_TX_VERSION,
            )
            tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
            batch.append(tx)

        local_nonce += batch_count

        # 7. Dry-run
        if dry_run:
            for tx in batch:
                val_egld = tx.value / 1e18
                print(f"[DRY-RUN] {wallet['name']} nonce={tx.nonce} "
                      f"value={val_egld:.5f} EGLD gas_price={tx.gas_price} → {receiver.to_bech32()[:16]}...")
            break

        # 8. Envoi bulk
        try:
            num_sent, _ = provider.send_transactions(batch)
            if num_sent < batch_count:
                print(f"[WARN] {wallet['name']}: {num_sent}/{batch_count} txs accepted")
        except Exception as e:
            print(f"[WARN] {wallet['name']}: batch error: {e}")

        # 9. Compteur global (log tous les 500)
        with lock:
            prev = counter[0]
            counter[0] += batch_count
            total = counter[0]
        if prev // 500 < total // 500:
            print(f"[INFO] {total:,} txs sent")


def main():
    parser = argparse.ArgumentParser(
        description="Burn EGLD Stress Test — gas premium conditionnel à la congestion du pool"
    )
    parser.add_argument("--wallets-dir", action="append", dest="wallets_dirs", required=True,
                        help="Dossier contenant les .pem (répétable)")
    parser.add_argument("--max-wallets", type=int, default=450)
    parser.add_argument("--amount", type=float, default=0.00005)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    if args.batch_size > MAX_NONCE_LOOKAHEAD:
        args.batch_size = MAX_NONCE_LOOKAHEAD - 5

    provider = utils.get_provider(args.gateway)
    amount_per_tx = int(args.amount * 1e18)

    wallets = load_wallets(args.wallets_dirs, args.max_wallets)
    n = len(wallets)

    print(f"[INFO] {n} wallets | amount/tx: {args.amount} EGLD | gateway: {args.gateway}")
    print(f"[INFO] Batch size: {args.batch_size} | POOL threshold: {POOL_CONGESTION_THRESHOLD:,} txs")
    print(f"[INFO] Gas: {config.DEFAULT_GAS_PRICE} (normal) / {PREMIUM_GAS_PRICE} (premium si pool congestionné)")
    if args.dry_run:
        print("[INFO] DRY-RUN\n")

    pool_congested = [False] * config.POOL_NUM_SHARDS  # un flag par shard
    stop_event = threading.Event()
    monitor = threading.Thread(
        target=pool_monitor,
        args=(args.gateway, pool_congested, stop_event),
        daemon=True,
    )
    monitor.start()

    counter = [0]
    lock = threading.Lock()
    api_semaphore = threading.Semaphore(MAX_CONCURRENT_API_CALLS)
    start = time.time()

    threads = [
        threading.Thread(
            target=wallet_worker,
            args=(i, wallets, amount_per_tx, args.batch_size,
                  provider, args.dry_run, counter, lock, api_semaphore, pool_congested, stop_event),
            daemon=True,
        )
        for i in range(n)
    ]

    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\n[INFO] Arrêt demandé — attente fin des threads...")
        stop_event.set()
        for t in threads:
            t.join(timeout=2)

    stop_event.set()

    elapsed = time.time() - start
    total = counter[0]
    rate = total / elapsed if elapsed > 0 else 0
    egld_burned = total * GAS_COST_RAW / 1e18
    print(f"\n[DONE] {total:,} txs en {elapsed:.1f}s ({rate:.0f} tx/s) | "
          f"~{egld_burned:.4f} EGLD brûlés en gas")


if __name__ == "__main__":
    main()
