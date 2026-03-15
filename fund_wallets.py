"""
Fund up to 100 challenge wallets from your main BoN wallet.

Reads all .pem files in a directory and sends EGLD to each address
from a single funding wallet. Nonce is tracked locally for speed.

Usage:
  python fund_wallets.py \\
    --from-wallet ./main_wallet.pem \\
    --wallets-dir ./wallets \\
    --amount 0.1

Options:
  --amount EGLD    Amount of EGLD to send to each wallet (default: 0.1)
  --dry-run        Preview transactions without sending


  python fund_wallets.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./wallets/shard-0 \
  --amount 0.05 \
  --max-wallets 30

    python fund_wallets.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./wallets/shard-1 \
  --amount 0.05 \
  --max-wallets 30

    python fund_wallets.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./wallets/shard-2 \
  --amount 0.05 \
  --max-wallets 30

  
  python send_tx.py --wallet ./wallets/bon_supernova.pem --to erd1avtfvajwhk3jk3d8a2c8fs65jya0ss2jap503f6l0c26s7sgrs7qduqf3m --value 2.0
  python send_tx.py --wallet ./wallets/bon_supernova.pem --to erd189c2pc8gskk6melsjp4zy623cj776c5ekgcuu7rwul0ez3hqcy0scmhx8v --value 2.0
  python send_tx.py --wallet ./wallets/bon_supernova.pem --to erd16c55f9d5k7tf2zzk7hwt6lv0acdt2vjfw2zc0nxf7wj6x0t9g7aqk8fgvq --value 2.0

  
      python fund_wallets.py \
    --from-wallet ./wallets/bon_supernova.pem \
    --wallets-dir ./sylla-wallets/shard-0 \
    --amount 4 \
    --max-wallets 100 \
    --dry-run

          python fund_wallets.py \
    --from-wallet ./wallets/bon_supernova.pem \
    --wallets-dir ./sylla-wallets/shard-1 \
    --amount 4 \
    --max-wallets 100 \
    --dry-run

          python fund_wallets.py \
    --from-wallet ./wallets/bon_supernova.pem \
    --wallets-dir ./sylla-wallets/shard-2 \
    --amount 4 \
    --max-wallets 100 \
    --dry-run

    python fund_wallets.py \
    --from-wallet ./wallets/bon_supernova.pem \
    --wallets-dir ./kelvin-wallets/shard-0 \
    --amount 4 \
    --max-wallets 100 \
    --dry-run

    python fund_wallets.py \
    --from-wallet ./wallets/bon_supernova.pem \
    --wallets-dir ./kelvin-wallets/shard-1 \
    --amount 4 \
    --max-wallets 100 \
    --dry-run

    python fund_wallets.py \
    --from-wallet ./wallets/bon_supernova.pem \
    --wallets-dir ./kelvin-wallets/shard-2 \
    --amount 4 \
    --max-wallets 100 \
    --dry-run



    python fund_wallets.py \
    --from-wallet ./wallets/bon_supernova.pem \
    --wallets-dir ./windowb-wallets/shard-0 \
    --amount 4 \
    --max-wallets 300 \
    --dry-run

    python fund_wallets.py \
    --from-wallet ./wallets/bon_supernova.pem \
    --wallets-dir ./windowb-wallets/shard-1 \
    --amount 4 \
    --max-wallets 300 \
    --dry-run

    python fund_wallets.py \
    --from-wallet ./wallets/bon_supernova.pem \
    --wallets-dir ./windowb-wallets/shard-0 \
    --amount 1 \
    --max-wallets 167 \
    --dry-run

        python fund_wallets.py \
    --from-wallet ./wallets/bon_supernova.pem \
    --wallets-dir ./windowb-wallets/shard-1 \
    --amount 1 \
    --max-wallets 167 \
    --dry-run

        python fund_wallets.py \
    --from-wallet ./wallets/bon_supernova.pem \
    --wallets-dir ./windowb-wallets/shard-2 \
    --amount 1 \
    --max-wallets 166 \
    --dry-run
  """
import argparse
import sys
import time
from pathlib import Path

from multiversx_sdk import Address, Transaction, TransactionComputer

import config
import utils

EGLD_DENOMINATION = 10 ** 18
GAS_TRANSFER = 50_000


def main():
    parser = argparse.ArgumentParser(description="Fund challenge wallets from main wallet")
    parser.add_argument("--from-wallet", required=True, help="Funding wallet PEM path")
    parser.add_argument("--wallets-dir", required=True, help="Directory with challenge wallet .pem files")
    parser.add_argument("--amount", type=float, default=0.1, help="EGLD per wallet (default: 0.1)")
    parser.add_argument("--max-wallets", type=int, default=100, help="Max wallets to fund (default: 100)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    provider = utils.get_provider(args.gateway)
    signer = utils.load_signer(args.from_wallet)
    sender = utils.get_address(signer)
    computer = TransactionComputer()

    # Collect target addresses from wallets dir
    pem_files = sorted(Path(args.wallets_dir).expanduser().glob("*.pem"))[:args.max_wallets]
    if not pem_files:
        print(f"[ERROR] No .pem files found in {args.wallets_dir}", file=sys.stderr)
        sys.exit(1)

    recipients = []
    for p in pem_files:
        w_signer = utils.load_signer(str(p))
        addr = utils.get_address(w_signer)
        # Skip if same as funding wallet
        if addr.to_bech32() != sender.to_bech32():
            recipients.append(addr)

    value_raw = int(args.amount * EGLD_DENOMINATION)
    total_egld = args.amount * len(recipients)

    print(f"[INFO] Funding wallet: {sender.to_bech32()}")
    print(f"[INFO] Recipients:     {len(recipients)} wallets")
    print(f"[INFO] Amount each:    {args.amount} EGLD")
    print(f"[INFO] Total needed:   {total_egld:.4f} EGLD (+ gas)")
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
            value=value_raw,
            gas_limit=GAS_TRANSFER,
            data=b"",
            chain_id=config.CHAIN_ID,
            gas_price=config.DEFAULT_GAS_PRICE,
            version=config.DEFAULT_TX_VERSION,
        )
        tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
        nonce += 1

        if args.dry_run:
            print(f"[DRY-RUN] {sender.to_bech32()[:12]}... → {addr.to_bech32()} | {args.amount} EGLD | nonce={nonce - 1}")
            sent += 1
            continue

        try:
            tx_hash = provider.send_transaction(tx)
            print(f"[OK] → {addr.to_bech32()} | {tx_hash}")
            sent += 1
        except Exception as e:
            print(f"[WARN] Failed to fund {addr.to_bech32()}: {e}")

    elapsed = time.time() - start
    print(f"\n[DONE] Funded {sent}/{len(recipients)} wallets in {elapsed:.1f}s")


if __name__ == "__main__":
    main()

