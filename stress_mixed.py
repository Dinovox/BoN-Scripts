"""
Stress test mixte intra + cross-shard — Battle of Nodes.

Objectif : 1 000 000 MoveBalance plain EGLD réussis, mix intra et cross-shard.

Architecture :
  - Jusqu'à 500 wallets d'envoi sur plusieurs shards (--wallets-dir répétable)
  - Chaque wallet a deux receivers : un intra-shard (round-robin dans son shard)
    et un cross-shard (round-robin dans les autres shards)
  - Ratio intra/cross configurable (--cross-ratio, défaut 0.5 = 50/50)
  - Décision prise par batch : chaque worker maintient son ratio courant
  - Bulk batching (send_transactions) — 1 requête HTTP pour 90 txs max
  - Ping-pong : les receivers renvoient vers d'autres wallets, les fonds sont recyclés
  - Backoff exponentiel si wallet vide, reprise automatique quand des fonds arrivent

Shard auto-détecté depuis l'adresse : last_byte_of_pubkey % num_shards

Usage :
  python stress_mixed.py \\
    --wallets-dir ./win4-wallets/shard-0 \\
    --wallets-dir ./win4-wallets/shard-1 \\
    --wallets-dir ./win4-wallets/shard-2 \\
    --max-wallets 500 \\
    --cross-ratio 0.5

  # Dry-run (1 batch par wallet, aucun envoi)
  python stress_mixed.py \\
    --wallets-dir ./win4-wallets/shard-0 \\
    --wallets-dir ./win4-wallets/shard-1 \\
    --wallets-dir ./win4-wallets/shard-2 \\
    --max-wallets 500 \\
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

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

GAS_MOVE_BALANCE   = 50_000
GAS_COST_RAW       = GAS_MOVE_BALANCE * config.DEFAULT_GAS_PRICE  # 0.00005 EGLD
DEFAULT_AMOUNT_PER_TX  = GAS_COST_RAW                              # 0.00005 EGLD
DEFAULT_BATCH_SIZE     = 90      # < MAX_NONCE_LOOKAHEAD (95) avec marge
MAX_NONCE_LOOKAHEAD    = 95
MAX_CONCURRENT_API_CALLS = 30

# Mode redistribution rapide quand un wallet est bien rempli
RICH_THRESHOLD = int(0.1 * 1e18)   # 0.1 EGLD
RICH_VALUE     = int(0.01 * 1e18)  # 0.01 EGLD par tx

NUM_SHARDS = 3  # shards utilisateur (0, 1, 2) — pas la metachain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compute_shard(address, num_shards: int = NUM_SHARDS) -> int:
    """Calcule le shard d'une adresse MultiversX (heuristique standard)."""
    last_byte = bytes.fromhex(address.to_hex())[-1]
    if last_byte == 0xFF:          # adresse metachain
        return num_shards
    return last_byte % num_shards


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
        signer  = utils.load_signer(str(p))
        address = utils.get_address(signer)
        shard   = compute_shard(address)
        wallets.append({
            "signer":  signer,
            "address": address,
            "name":    p.name,
            "shard":   shard,
        })
    return wallets


def build_routing(wallets: list[dict]) -> list[dict]:
    """
    Pour chaque wallet, assigne :
      - intra_receiver : prochain wallet du même shard (round-robin)
      - cross_receiver : un wallet d'un autre shard (round-robin sur les autres shards)
    Si un seul shard est disponible, cross_receiver = intra_receiver (fallback).
    """
    # Grouper par shard
    by_shard: dict[int, list[dict]] = {}
    for w in wallets:
        by_shard.setdefault(w["shard"], []).append(w)

    for shard_id, group in by_shard.items():
        # Wallets des autres shards, fusionnés
        cross_pool = []
        for other_shard, other_group in by_shard.items():
            if other_shard != shard_id:
                cross_pool.extend(other_group)

        for local_idx, w in enumerate(group):
            # Intra : wallet suivant dans le même shard
            w["intra_receiver"] = group[(local_idx + 1) % len(group)]["address"]

            # Cross : wallet d'un autre shard, rotation simple
            if cross_pool:
                w["cross_receiver"] = cross_pool[local_idx % len(cross_pool)]["address"]
            else:
                w["cross_receiver"] = w["intra_receiver"]  # fallback mono-shard

    return wallets


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def wallet_worker(
    wallet: dict,
    amount_per_tx: int,
    batch_size: int,
    cross_ratio: float,
    provider,
    dry_run: bool,
    counter: list,       # [total, intra, cross]
    lock: threading.Lock,
    api_semaphore: threading.Semaphore,
):
    signer   = wallet["signer"]
    sender   = wallet["address"]
    computer = TransactionComputer()

    intra_receiver = wallet["intra_receiver"]
    cross_receiver = wallet["cross_receiver"]

    try:
        confirmed_nonce, _ = get_account_safe(provider, sender, api_semaphore)
    except Exception as e:
        print(f"[ERROR] Init failed for {wallet['name']}: {e}")
        return

    local_nonce  = confirmed_nonce
    local_intra  = 0
    local_cross  = 0
    empty_streak = 0

    while True:
        # 1. Balance + nonce confirmé
        try:
            confirmed_nonce, balance = get_account_safe(provider, sender, api_semaphore)
        except Exception:
            time.sleep(0.5)
            continue

        # 2. Effective value : redistribution rapide si wallet riche
        effective_value = RICH_VALUE if balance >= RICH_THRESHOLD else amount_per_tx

        # 3. Stop si wallet épuisé
        pending_count     = local_nonce - confirmed_nonce
        available_balance = max(0, balance - pending_count * (GAS_COST_RAW + effective_value))
        if available_balance < GAS_COST_RAW:
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

        # 5. Décision intra vs cross pour ce batch
        local_total = local_intra + local_cross
        if local_total == 0:
            use_cross = cross_ratio >= 0.5   # premier batch : choisir selon ratio dominant
        else:
            current_cross_ratio = local_cross / local_total
            use_cross = current_cross_ratio < cross_ratio

        receiver  = cross_receiver if use_cross else intra_receiver

        # 6. Décision value + taille du batch
        if available_balance >= GAS_COST_RAW + effective_value:
            value          = effective_value
            max_by_balance = available_balance // (GAS_COST_RAW + effective_value)
        else:
            value          = 0
            max_by_balance = 1

        batch_count = min(batch_size, nonce_slots, max_by_balance)
        if batch_count == 0:
            time.sleep(0.5)
            continue

        # 7. Build + sign le batch
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
                gas_price=config.DEFAULT_GAS_PRICE,
                version=config.DEFAULT_TX_VERSION,
            )
            tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
            batch.append(tx)

        local_nonce += batch_count

        # 8. Dry-run : afficher et arrêter
        if dry_run:
            tag = "CROSS" if use_cross else "INTRA"
            for tx in batch:
                val_egld = tx.value / 1e18
                print(f"[DRY-RUN][{tag}] {wallet['name']} shard={wallet['shard']} "
                      f"nonce={tx.nonce} value={val_egld:.5f} EGLD "
                      f"→ {receiver.to_bech32()[:20]}...")
            break

        # 9. Envoi bulk
        try:
            num_sent, _ = provider.send_transactions(batch)
            if num_sent < batch_count:
                print(f"[WARN] {wallet['name']}: {num_sent}/{batch_count} txs accepted")
        except Exception as e:
            print(f"[WARN] {wallet['name']}: batch error: {e}")
            continue

        # 10. Mise à jour compteurs
        if use_cross:
            local_cross += batch_count
        else:
            local_intra += batch_count

        with lock:
            prev = counter[0]
            counter[0] += batch_count
            counter[1] += 0 if use_cross else batch_count
            counter[2] += batch_count if use_cross else 0
            total = counter[0]

        if prev // 1000 < total // 1000:
            with lock:
                intra_pct = (counter[1] / counter[0] * 100) if counter[0] else 0
                cross_pct  = (counter[2] / counter[0] * 100) if counter[0] else 0
            print(f"[INFO] {total:,} txs sent  "
                  f"(intra: {counter[1]:,} {intra_pct:.0f}%  "
                  f"cross: {counter[2]:,} {cross_pct:.0f}%)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Stress test mixte intra + cross-shard — plain EGLD MoveBalance"
    )
    parser.add_argument(
        "--wallets-dir", action="append", dest="wallets_dirs", required=True,
        help="Dossier contenant les .pem (répétable, un par shard de préférence)"
    )
    parser.add_argument("--max-wallets", type=int, default=500,
                        help="Max wallets à utiliser (défaut: 500)")
    parser.add_argument("--amount", type=float, default=0.00005,
                        help="EGLD envoyé par tx (défaut: 0.00005)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Txs par requête HTTP (défaut: {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--cross-ratio", type=float, default=0.5,
                        help="Fraction de txs cross-shard (0.0–1.0, défaut: 0.5)")
    parser.add_argument("--num-shards", type=int, default=NUM_SHARDS,
                        help="Nombre de shards utilisateur (défaut: 3)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Construire/signer 1 batch par wallet sans envoyer")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    if not 0.0 <= args.cross_ratio <= 1.0:
        print("[ERROR] --cross-ratio doit être entre 0.0 et 1.0", file=sys.stderr)
        sys.exit(1)

    if args.batch_size > MAX_NONCE_LOOKAHEAD:
        print(f"[WARN] --batch-size {args.batch_size} > MAX_NONCE_LOOKAHEAD {MAX_NONCE_LOOKAHEAD}, "
              f"réduit à {MAX_NONCE_LOOKAHEAD - 5}")
        args.batch_size = MAX_NONCE_LOOKAHEAD - 5

    provider       = utils.get_provider(args.gateway)
    amount_per_tx  = int(args.amount * 1e18)

    wallets = load_wallets(args.wallets_dirs, args.max_wallets)
    wallets = build_routing(wallets)

    # Résumé par shard
    by_shard: dict[int, int] = {}
    for w in wallets:
        by_shard[w["shard"]] = by_shard.get(w["shard"], 0) + 1

    n = len(wallets)
    intra_ratio = 1.0 - args.cross_ratio

    print(f"[INFO] {n} wallets | par shard : { {k: v for k, v in sorted(by_shard.items())} }")
    print(f"[INFO] Ratio : {intra_ratio*100:.0f}% intra  /  {args.cross_ratio*100:.0f}% cross")
    print(f"[INFO] amount/tx: {args.amount} EGLD | gas/tx: {GAS_COST_RAW / 1e18:.5f} EGLD")
    print(f"[INFO] Batch size: {args.batch_size} | gateway: {args.gateway}")
    if args.dry_run:
        print("[INFO] DRY-RUN — 1 batch par wallet, aucun envoi\n")
    else:
        print("[INFO] Démarrage — arrêt automatique quand tous les wallets sont vides\n")

    counter = [0, 0, 0]   # [total, intra, cross]
    lock    = threading.Lock()
    api_semaphore = threading.Semaphore(MAX_CONCURRENT_API_CALLS)
    start   = time.time()

    threads = [
        threading.Thread(
            target=wallet_worker,
            args=(w, amount_per_tx, args.batch_size, args.cross_ratio,
                  provider, args.dry_run, counter, lock, api_semaphore),
            daemon=True,
        )
        for w in wallets
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    elapsed    = time.time() - start
    total      = counter[0]
    rate       = total / elapsed if elapsed > 0 else 0
    egld_burned = total * GAS_COST_RAW / 1e18
    intra_pct  = counter[1] / total * 100 if total else 0
    cross_pct  = counter[2] / total * 100 if total else 0

    print(f"\n[DONE] {total:,} txs en {elapsed:.1f}s ({rate:.0f} tx/s)")
    print(f"       intra: {counter[1]:,} ({intra_pct:.1f}%)  "
          f"cross: {counter[2]:,} ({cross_pct:.1f}%)")
    print(f"       ~{egld_burned:.4f} EGLD brûlés en gas")


if __name__ == "__main__":
    main()
