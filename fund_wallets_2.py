"""
Fund challenge wallets from main wallet — envoi en batch (send_transactions).

Identique à fund_wallets.py mais prépare toutes les txs en mémoire puis les
soumet par batches de BATCH_SIZE via provider.send_transactions() :
1 requête HTTP par batch au lieu de 1 par tx → ne bloque plus au-delà de 100 tx.

Usage:
  python fund_wallets_2.py \\
    --from-wallet ./wallets/bon_supernova.pem \\
    --wallets-dir ./windowb-wallets/shard-0 \\
    --amount 1 \\
    --max-wallets 500 \\
    --dry-run
"""
import argparse
import sys
import time
from pathlib import Path

from multiversx_sdk import Transaction, TransactionComputer

import config
import utils

EGLD_DENOMINATION = 10 ** 18
GAS_TRANSFER = 50_000
BATCH_SIZE = 90


def main():
    parser = argparse.ArgumentParser(description="Fund challenge wallets (batch mode)")
    parser.add_argument("--from-wallet", required=True, help="Funding wallet PEM path")
    parser.add_argument("--wallets-dir", action="append", dest="wallets_dirs", required=True,
                        help="Dossier contenant des .pem (répétable)")
    parser.add_argument("--amount", type=float, default=0.1, help="EGLD par wallet (défaut: 0.1)")
    parser.add_argument("--max-wallets", type=int, default=0,
                        help="Limite de wallets par dossier (0 = tous)")
    parser.add_argument("--dry-run", action="store_true", help="Afficher sans envoyer")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    provider = utils.get_provider(args.gateway)
    signer = utils.load_signer(args.from_wallet)
    sender = utils.get_address(signer)
    sender_bech32 = sender.to_bech32()
    computer = TransactionComputer()

    # Collecter tous les .pem
    all_pems = []
    for d in args.wallets_dirs:
        pems = sorted(Path(d).expanduser().glob("*.pem"))
        if args.max_wallets:
            pems = pems[:args.max_wallets]
        all_pems.extend(pems)

    if not all_pems:
        print("[ERROR] Aucun .pem trouvé", file=sys.stderr)
        sys.exit(1)

    # Dériver les adresses destinataires
    recipients = []
    for p in all_pems:
        w_signer = utils.load_signer(str(p))
        addr = utils.get_address(w_signer)
        if addr.to_bech32() != sender_bech32:
            recipients.append(addr)

    value_raw = int(args.amount * EGLD_DENOMINATION)
    total_egld = args.amount * len(recipients)

    print(f"[INFO] Funding wallet: {sender_bech32}")
    print(f"[INFO] Destinataires:  {len(recipients)} wallets")
    print(f"[INFO] Montant:        {args.amount} EGLD chacun")
    print(f"[INFO] Total:          {total_egld:.4f} EGLD (+ gas)")
    print(f"[INFO] Batch size:     {BATCH_SIZE}")
    if args.dry_run:
        print("[INFO] DRY-RUN — aucune transaction ne sera envoyée\n")

    nonce = utils.get_account_nonce(provider, sender)
    print(f"[INFO] Nonce de départ: {nonce}\n")

    # Construire toutes les txs
    txs = []
    for addr in recipients:
        tx = Transaction(
            nonce=nonce,
            sender=sender,
            receiver=addr,
            value=value_raw,
            gas_limit=GAS_TRANSFER,
            data=b"",
            chain_id=config.CHAIN_ID,
            gas_price=config.DEFAULT_GAS_PRICE,
            version=config.DEFAULT_TX_VERSION,
        )
        tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
        txs.append(tx)
        nonce += 1

    if args.dry_run:
        for tx in txs:
            print(f"[DRY-RUN] nonce={tx.nonce} → {tx.receiver.to_bech32()} | {args.amount} EGLD")
        print(f"\n[DRY-RUN] {len(txs)} transactions préparées (non envoyées)")
        return

    # Envoyer par batches
    sent = 0
    start = time.time()
    for i in range(0, len(txs), BATCH_SIZE):
        batch = txs[i:i + BATCH_SIZE]
        try:
            num_sent, _ = provider.send_transactions(batch)
            sent += num_sent
            print(f"[OK] batch {i // BATCH_SIZE + 1}: {num_sent}/{len(batch)} txs acceptées (total: {sent})")
            if num_sent < len(batch):
                print(f"[WARN] {len(batch) - num_sent} txs refusées dans ce batch")
        except Exception as e:
            print(f"[WARN] batch {i // BATCH_SIZE + 1} erreur: {e}")

    elapsed = time.time() - start
    print(f"\n[DONE] {sent}/{len(txs)} txs envoyées en {elapsed:.1f}s")


if __name__ == "__main__":
    main()
