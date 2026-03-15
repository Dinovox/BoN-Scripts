"""
Distribue des tokens ESDT depuis un wallet principal vers N wallets de challenge.

Fonctionne comme fund_wallets.py mais utilise ESDTTransfer au lieu d'un simple
transfert EGLD. Compatible avec tout token ESDT (WEGLD, USDC, etc.).

Usage:
  python fund_esdt.py \\
    --from-wallet ./wallets/bon_supernova.pem \\
    --wallets-dir ./wallets/shard-1 \\
    --token-id WEGLD-bd4d79 \\
    --amount 0.05

  # Dry-run
  python fund_esdt.py \\
    --from-wallet ./wallets/bon_supernova.pem \\
    --wallets-dir ./wallets/shard-1 \\
    --token-id WEGLD-bd4d79 \\
    --amount 0.05 --dry-run

    python fund_esdt.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./wallets/shard-0 \
  --token-id WEGLD-bd4d79 \
  --max-wallets 30 \
  --amount 0.05 

  python fund_esdt.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./wallets/shard-1 \
  --token-id WEGLD-bd4d79 \
  --max-wallets 30 \
  --amount 0.05 --dry-run

  python fund_esdt.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./wallets/shard-2 \
  --token-id WEGLD-bd4d79 \
  --max-wallets 30 \
  --amount 0.05 --dry-run
"""
import argparse
import sys
import time
from pathlib import Path

from multiversx_sdk import Transaction, TransactionComputer

import config
import utils

GAS_ESDT_TRANSFER = 500_000


def encode_amount_hex(amount: int) -> str:
    """Encode integer as minimal big-endian hex (at least 1 byte)."""
    if amount == 0:
        return "00"
    h = hex(amount)[2:]
    return h if len(h) % 2 == 0 else "0" + h


def build_esdt_data(token_id: str, amount_raw: int) -> bytes:
    """Build ESDTTransfer data field."""
    token_hex = token_id.encode().hex()
    amount_hex = encode_amount_hex(amount_raw)
    return f"ESDTTransfer@{token_hex}@{amount_hex}".encode()


def main():
    parser = argparse.ArgumentParser(description="Distribue des tokens ESDT vers des wallets de challenge")
    parser.add_argument("--from-wallet", required=True, help="Wallet expéditeur PEM")
    parser.add_argument("--wallets-dir", required=True, help="Dossier contenant les .pem destinataires")
    parser.add_argument("--token-id", required=True, help="Identifiant du token (ex: WEGLD-bd4d79)")
    parser.add_argument("--amount", type=float, required=True, help="Montant par wallet (unités humaines)")
    parser.add_argument("--decimals", type=int, default=18, help="Décimales du token (défaut: 18)")
    parser.add_argument("--max-wallets", type=int, default=100, help="Max wallets à financer (défaut: 100)")
    parser.add_argument("--gas", type=int, default=GAS_ESDT_TRANSFER,
                        help=f"Gas limit par tx (défaut: {GAS_ESDT_TRANSFER:,})")
    parser.add_argument("--dry-run", action="store_true", help="Afficher sans envoyer")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    provider = utils.get_provider(args.gateway)
    signer = utils.load_signer(args.from_wallet)
    sender = utils.get_address(signer)
    computer = TransactionComputer()

    # Collecter les adresses destinataires
    pem_files = sorted(Path(args.wallets_dir).expanduser().glob("*.pem"))[:args.max_wallets]
    if not pem_files:
        print(f"[ERROR] No .pem files found in {args.wallets_dir}", file=sys.stderr)
        sys.exit(1)

    recipients = []
    for p in pem_files:
        w_signer = utils.load_signer(str(p))
        addr = utils.get_address(w_signer)
        if addr.to_bech32() != sender.to_bech32():
            recipients.append(addr)

    amount_raw = int(args.amount * (10 ** args.decimals))
    data = build_esdt_data(args.token_id, amount_raw)
    total_tokens = args.amount * len(recipients)

    print(f"[INFO] Sender:       {sender.to_bech32()}")
    print(f"[INFO] Token:        {args.token_id}")
    print(f"[INFO] Amount each:  {args.amount} ({amount_raw} raw)")
    print(f"[INFO] Recipients:   {len(recipients)} wallets")
    print(f"[INFO] Total needed: {total_tokens:.4f} {args.token_id}")
    print(f"[INFO] Data:         {data.decode()}")
    if args.dry_run:
        print("[INFO] DRY-RUN – transactions will NOT be sent\n")

    nonce = utils.get_account_nonce(provider, sender)
    print(f"[INFO] Starting nonce: {nonce}\n")

    sent = 0
    start = time.time()
    for addr in recipients:
        tx = Transaction(
            nonce=nonce,
            sender=sender,
            receiver=addr,
            value=0,
            gas_limit=args.gas,
            data=data,
            chain_id=config.CHAIN_ID,
            gas_price=config.DEFAULT_GAS_PRICE,
            version=config.DEFAULT_TX_VERSION,
        )
        tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
        nonce += 1

        if args.dry_run:
            print(f"[DRY-RUN] → {addr.to_bech32()} | {args.amount} {args.token_id} | nonce={nonce - 1}")
            sent += 1
            continue

        try:
            tx_hash = provider.send_transaction(tx)
            print(f"[OK] → {addr.to_bech32()} | {tx_hash}")
            sent += 1
        except Exception as e:
            print(f"[WARN] Failed for {addr.to_bech32()}: {e}")

    elapsed = time.time() - start
    print(f"\n[DONE] Sent {sent}/{len(recipients)} ESDT transfers in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
