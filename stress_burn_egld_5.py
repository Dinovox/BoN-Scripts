"""
Burn EGLD Stress Test: brûler le maximum d'EGLD intra-shard.

450 wallets du même shard se passent de l'EGLD en round-robin (wallet[i] → wallet[i+1]).
Chaque transaction brûle 0.00005 EGLD de gas. Les transactions sont envoyées en bulk
(provider.send_transactions) : 1 requête HTTP par batch de 90 txs au lieu de 1 par tx.

La balance est vérifiée une fois par batch (observing squad local → API rapide).

Logique par batch :
  - balance ≥ (gas + amount) × batch_size  → batch complet, value = amount
  - gas ≤ balance < (gas + amount)          → 1 tx gas-only (dernier brûlage)
  - balance < gas                            → wallet épuisé, thread s'arrête

Arrêt global quand tous les wallets sont vides.

Usage:
  python stress_burn_egld.py \
    --wallets-dir ./guild-wallets/shard-0 \
    --max-wallets 450

  # Dry-run (1 batch par wallet, aucun envoi)
  python stress_burn_egld.py \
    --wallets-dir ./windowb-wallets/shard-0 \
    --wallets-dir ./windowb-wallets/shard-1 \
    --wallets-dir ./windowb-wallets/shard-2 \
    --max-wallets 500 \
    --batch-size 95 \
    --dry-run


      python stress_burn_egld.py \
    --wallets-dir ./windowb-wallets/shard-0 \
    --max-wallets 167 \
    --batch-size 95 \
    --dry-run



    
"""
import argparse
import sys
import threading
import time
from pathlib import Path

from multiversx_sdk import Transaction, TransactionComputer

import config
import utils

GAS_MOVE_BALANCE = 50_000
GAS_COST_RAW = GAS_MOVE_BALANCE * config.DEFAULT_GAS_PRICE   # 0.00005 EGLD en raw
DEFAULT_AMOUNT_PER_TX = GAS_COST_RAW                          # 0.00005 EGLD
DEFAULT_BATCH_SIZE = 90        # < MAX_NONCE_LOOKAHEAD (95) avec marge
MAX_NONCE_LOOKAHEAD = 95
MAX_CONCURRENT_API_CALLS = 30


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
):
    wallet = all_wallets[idx]
    signer = wallet["signer"]
    sender = wallet["address"]
    n = len(all_wallets)
    receiver = all_wallets[(idx + 1) % n]["address"]
    computer = TransactionComputer()

    try:
        confirmed_nonce, _ = get_account_safe(provider, sender, api_semaphore)
    except Exception as e:
        print(f"[ERROR] Init failed for {wallet['name']}: {e}")
        return

    local_nonce = confirmed_nonce

    while True:
        # 1. Balance + nonce confirmé (1 appel API par batch)
        try:
            confirmed_nonce, balance = get_account_safe(provider, sender, api_semaphore)
        except Exception:
            time.sleep(0.5)
            continue

        # 2. Stop si wallet épuisé
        if balance == 0 or balance < GAS_COST_RAW:
            break

        # 3. Slots nonce disponibles
        nonce_slots = MAX_NONCE_LOOKAHEAD - (local_nonce - confirmed_nonce)
        if nonce_slots <= 0:
            time.sleep(0.3)
            continue

        # 4. Décision value + taille du batch
        if balance >= GAS_COST_RAW + amount_per_tx:
            value = amount_per_tx
            max_by_balance = balance // (GAS_COST_RAW + amount_per_tx)
        else:
            value = 0           # dernier brûlage : gas seul
            max_by_balance = 1  # une seule tx pour vider le reste

        batch_count = min(batch_size, nonce_slots, max_by_balance)
        if batch_count == 0:
            break

        # 5. Build + sign le batch
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
                gas_price=config.DEFAULT_GAS_PRICE ,  # premium si value > 0
                version=config.DEFAULT_TX_VERSION,
            )
            tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
            batch.append(tx)

        local_nonce += batch_count

        # 6. Dry-run : afficher le batch et arrêter
        if dry_run:
            for tx in batch:
                val_egld = tx.value / 1e18
                print(f"[DRY-RUN] {wallet['name']} nonce={tx.nonce} "
                      f"value={val_egld:.5f} EGLD → {receiver.to_bech32()[:16]}...")
            break

        # 7. Envoi bulk (1 requête HTTP)
        try:
            num_sent, _ = provider.send_transactions(batch)
            if num_sent < batch_count:
                print(f"[WARN] {wallet['name']}: {num_sent}/{batch_count} txs accepted")
        except Exception as e:
            print(f"[WARN] {wallet['name']}: batch error: {e}")

        # 8. Compteur global (log tous les 500)
        with lock:
            prev = counter[0]
            counter[0] += batch_count
            total = counter[0]
        if prev // 500 < total // 500:
            print(f"[INFO] {total:,} txs sent")


def main():
    parser = argparse.ArgumentParser(description="Burn EGLD Stress Test — intra-shard bulk batch")
    parser.add_argument("--wallets-dir", action="append", dest="wallets_dirs", required=True,
                        help="Dossier contenant les .pem (répétable)")
    parser.add_argument("--max-wallets", type=int, default=450,
                        help="Max wallets à utiliser (défaut: 450)")
    parser.add_argument("--amount", type=float, default=0.00005,
                        help="EGLD envoyé par tx (défaut: 0.00005)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Txs par requête HTTP (défaut: {DEFAULT_BATCH_SIZE}, max: {MAX_NONCE_LOOKAHEAD})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Construire/signer 1 batch par wallet sans envoyer")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    if args.batch_size > MAX_NONCE_LOOKAHEAD:
        print(f"[WARN] --batch-size {args.batch_size} > MAX_NONCE_LOOKAHEAD {MAX_NONCE_LOOKAHEAD}, "
              f"réduit à {MAX_NONCE_LOOKAHEAD - 5}")
        args.batch_size = MAX_NONCE_LOOKAHEAD - 5

    provider = utils.get_provider(args.gateway)
    amount_per_tx = int(args.amount * 1e18)

    wallets = load_wallets(args.wallets_dirs, args.max_wallets)
    n = len(wallets)

    print(f"[INFO] {n} wallets | amount/tx: {args.amount} EGLD | gas/tx: {GAS_COST_RAW / 1e18:.5f} EGLD")
    print(f"[INFO] Batch size: {args.batch_size} txs/requête | gateway: {args.gateway}")
    print(f"[INFO] Routing: round-robin intra-shard (wallet[i] → wallet[i+1])")
    if args.dry_run:
        print("[INFO] DRY-RUN — 1 batch par wallet, aucun envoi\n")
    else:
        print("[INFO] Démarrage — arrêt automatique quand tous les wallets sont vides\n")

    counter = [0]
    lock = threading.Lock()
    api_semaphore = threading.Semaphore(MAX_CONCURRENT_API_CALLS)
    start = time.time()

    threads = [
        threading.Thread(
            target=wallet_worker,
            args=(i, wallets, amount_per_tx, args.batch_size,
                  provider, args.dry_run, counter, lock, api_semaphore),
            daemon=True,
        )
        for i in range(n)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    elapsed = time.time() - start
    total = counter[0]
    rate = total / elapsed if elapsed > 0 else 0
    egld_burned = total * GAS_COST_RAW / 1e18
    print(f"\n[DONE] {total:,} txs en {elapsed:.1f}s ({rate:.0f} tx/s) | "
          f"~{egld_burned:.4f} EGLD brûlés en gas")


if __name__ == "__main__":
    main()
