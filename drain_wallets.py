"""
Vide tous les wallets d'un ou plusieurs dossiers et renvoie les fonds vers une adresse cible.

Pour chaque wallet :
  - balance <= gas_cost → skippé (trop peu pour couvrir le gas)
  - sinon → 1 tx plain EGLD : value = balance - gas_cost, data vide

Envoi parallèle (1 thread par wallet).

Usage :
  python drain_wallets.py \
    --wallets-dir ./spam-wallets/shard-0 \
    --wallets-dir ./spam-wallets/shard-1 \
    --wallets-dir ./spam-wallets/shard-2 \
    --to erd1xxxxxxxxx... \
    --max-wallets 500 \
    --gateway http://192.168.1.23:8079

  # Dry-run (affiche sans envoyer)
  python drain_wallets.py \
    --wallets-dir ./spam-wallets/shard-0 \
    --to erd1xxxxxxxxx... \
    --dry-run
"""
import argparse
import sys
import threading
from pathlib import Path

import requests
from multiversx_sdk import Address, Transaction, TransactionComputer

import config
import utils

GAS_MOVE_BALANCE = 50_000
GAS_COST_RAW     = GAS_MOVE_BALANCE * config.DEFAULT_GAS_PRICE   # 0.00005 EGLD

MAX_CONCURRENT_API_CALLS = 30


def get_balance_nonce(gateway_url: str, address, api_semaphore) -> tuple[int, int]:
    """Retourne (nonce, balance_raw) via HTTP direct (sans guardian-data)."""
    url = f"{gateway_url.rstrip('/')}/address/{address.to_bech32()}"
    with api_semaphore:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", {}).get("account", {})
        return int(data.get("nonce", 0)), int(data.get("balance", 0))


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
        wallets.append({"signer": signer, "address": address, "name": p.name})
    return wallets


def drain_worker(
    wallet: dict,
    target_address,
    gateway_url: str,
    provider,
    dry_run: bool,
    counter: list,    # [sent, skipped, errors]
    lock: threading.Lock,
    api_semaphore: threading.Semaphore,
):
    signer   = wallet["signer"]
    sender   = wallet["address"]
    computer = TransactionComputer()

    # 1. Fetch balance + nonce
    try:
        nonce, balance = get_balance_nonce(gateway_url, sender, api_semaphore)
    except Exception as e:
        print(f"[ERROR] {wallet['name']}: lecture balance échouée : {e}")
        with lock:
            counter[2] += 1
        return

    # 2. Skip si pas assez pour couvrir le gas
    if balance <= GAS_COST_RAW:
        egld = balance / 1e18
        if balance > 0:
            print(f"[SKIP]  {wallet['name']}  {egld:.6f} EGLD (< gas cost)")
        with lock:
            counter[1] += 1
        return

    # 3. Build tx : value = tout sauf le gas
    value = balance - GAS_COST_RAW
    tx = Transaction(
        nonce=nonce,
        sender=sender,
        receiver=target_address,
        value=value,
        gas_limit=GAS_MOVE_BALANCE,
        data=b"",
        chain_id=config.CHAIN_ID,
        gas_price=config.DEFAULT_GAS_PRICE,
        version=config.DEFAULT_TX_VERSION,
    )
    tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))

    egld = value / 1e18
    dest = target_address.to_bech32()[:20]

    # 4. Dry-run ou envoi
    if dry_run:
        print(f"[DRY-RUN] {wallet['name']:<25}  {egld:.6f} EGLD  →  {dest}...")
        with lock:
            counter[0] += 1
        return

    try:
        provider.send_transaction(tx)
        print(f"[SENT]  {wallet['name']:<25}  {egld:.6f} EGLD  →  {dest}...")
        with lock:
            counter[0] += 1
    except Exception as e:
        print(f"[ERROR] {wallet['name']}: envoi échoué : {e}")
        with lock:
            counter[2] += 1


def main():
    parser = argparse.ArgumentParser(
        description="Vider tous les wallets d'un dossier vers une adresse cible"
    )
    parser.add_argument("--wallets-dir", action="append", dest="wallets_dirs", required=True,
                        help="Dossier contenant les .pem (répétable)")
    parser.add_argument("--to", required=True, metavar="ERD1_ADDRESS",
                        help="Adresse de destination (bech32 erd1...)")
    parser.add_argument("--max-wallets", type=int, default=0,
                        help="Limiter le nombre de wallets (0 = tous)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Afficher les txs sans les envoyer")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    try:
        target_address = Address.new_from_bech32(args.to)
    except Exception:
        print(f"[ERROR] Adresse invalide : {args.to}", file=sys.stderr)
        sys.exit(1)

    max_w    = args.max_wallets or 999_999
    wallets  = load_wallets(args.wallets_dirs, max_w)
    provider = utils.get_provider(args.gateway)

    print(f"[INFO] {len(wallets)} wallets | destination : {args.to}")
    print(f"[INFO] gateway : {args.gateway}")
    if args.dry_run:
        print("[INFO] DRY-RUN — aucune transaction ne sera envoyée\n")
    else:
        print("[INFO] Envoi en cours...\n")

    counter       = [0, 0, 0]   # [sent, skipped, errors]
    lock          = threading.Lock()
    api_semaphore = threading.Semaphore(MAX_CONCURRENT_API_CALLS)

    threads = [
        threading.Thread(
            target=drain_worker,
            args=(w, target_address, args.gateway, provider,
                  args.dry_run, counter, lock, api_semaphore),
            daemon=True,
        )
        for w in wallets
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    tag = "[DRY-RUN]" if args.dry_run else "[DONE]"
    print(f"\n{tag}  envoyés: {counter[0]}  skippés: {counter[1]}  erreurs: {counter[2]}")


if __name__ == "__main__":
    main()
