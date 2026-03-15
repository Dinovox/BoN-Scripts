"""
Window B – Stress Test: 1,000 DEX swapTokensFixedInput

Sends swapTokensFixedInput calls to a BoN DEX pool via ESDTTransfer.

Pool:     erd1qqqqqqqqqqqqqpgqeel2kumf0r8ffyhth7pqdujjat9nx0862jpsg2pqaq
Function: swapTokensFixedInput

Usage:
  python stress_dex_swap.py \\
    --wallet ./wallets/wallet_00.pem \\
    --token-in  WEGLD-abcdef \\
    --amount-in 0.001 \\
    --token-out USDC-abcdef \\
    [--min-out 1] [--target 1000] [--dry-run]

Notes:
  - The wallet must hold enough of --token-in to cover target * amount-in
  - Gas is ~15,000,000 per swap; override with --gas
  - Token decimals default to 18; use --decimals for other tokens
  - Use multiple --wallet flags to spread load across wallets
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
MAX_NONCE_LOOKAHEAD = 95  # MultiversX rejects nonce if > maxNonceDelta ahead of confirmed


def encode_hex(value: str | bytes) -> str:
    """Encode string or bytes to hex."""
    if isinstance(value, str):
        return value.encode().hex()
    return value.hex()


def encode_amount_hex(amount: int) -> str:
    """Encode integer amount as minimal big-endian hex (at least 1 byte)."""
    if amount == 0:
        return "00"
    h = hex(amount)[2:]
    return h if len(h) % 2 == 0 else "0" + h


def build_swap_data(token_in: str, amount_in_raw: int, token_out: str, min_out: int) -> str:
    """
    Build the data field for ESDTTransfer + swapTokensFixedInput.

    Format (MultiversX):
      ESDTTransfer@<tokenIn_hex>@<amountIn_hex>@<function_hex>@<tokenOut_hex>@<minOut_hex>
    """
    return "@".join([
        "ESDTTransfer",
        encode_hex(token_in),
        encode_amount_hex(amount_in_raw),
        encode_hex("swapTokensFixedInput"),
        encode_hex(token_out),
        encode_amount_hex(min_out),
    ])


def wallet_worker(
    wallet_pem: str,
    txs_to_send: int,
    data_str: str,
    gas_limit: int,
    provider,
    dry_run: bool,
    counter: list,
    lock: threading.Lock,
):
    signer = utils.load_signer(wallet_pem)
    sender = utils.get_address(signer)
    computer = TransactionComputer()

    try:
        nonce = utils.get_account_nonce(provider, sender)
    except Exception as e:
        print(f"[ERROR] Nonce fetch failed for {sender.to_bech32()[:12]}...: {e}")
        return

    print(f"[INFO] {sender.to_bech32()[:16]}... starting nonce={nonce}, txs={txs_to_send}")

    confirmed_nonce = nonce

    for _ in range(txs_to_send):
        # Pause if local nonce is too far ahead of confirmed on-chain nonce
        while not dry_run and nonce - confirmed_nonce >= MAX_NONCE_LOOKAHEAD:
            time.sleep(0.5)
            try:
                confirmed_nonce = utils.get_account_nonce(provider, sender)
            except Exception:
                pass

        tx = Transaction(
            nonce=nonce,
            sender=sender,
            receiver=Address.new_from_bech32(DEX_POOL),
            value=0,
            gas_limit=gas_limit,
            data=data_str.encode(),
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
                print(f"[WARN] {sender.to_bech32()[:12]}... nonce={nonce - 1}: {e}")

        with lock:
            counter[0] += 1
            total = counter[0]
        if total % 100 == 0:
            tag = "[DRY-RUN]" if dry_run else "[INFO]"
            print(f"{tag} {total} swap txs sent")


def main():
    parser = argparse.ArgumentParser(description="Window B – 1,000 DEX swapTokensFixedInput calls")
    wallet_group = parser.add_mutually_exclusive_group(required=True)
    wallet_group.add_argument("--wallet", action="append", dest="wallets",
                              help="Path to PEM wallet (repeat for multiple wallets)")
    wallet_group.add_argument("--wallets-dir", dest="wallets_dir",
                              help="Directory containing .pem wallet files")
    parser.add_argument("--max-wallets", type=int, default=100,
                        help="Max wallets to use from --wallets-dir (default: 100)")
    parser.add_argument("--token-in", required=True, help="Input token ID (e.g. WEGLD-bd4d79)")
    parser.add_argument("--amount-in", type=float, required=True,
                        help="Amount of input token per swap (in human units)")
    parser.add_argument("--token-out", required=True, help="Output token ID (e.g. USDC-c76f1f)")
    parser.add_argument("--min-out", type=int, default=1,
                        help="Minimum output amount in raw units (default: 1)")
    parser.add_argument("--decimals", type=int, default=18,
                        help="Input token decimals (default: 18 for WEGLD/EGLD)")
    parser.add_argument("--target", type=int, default=1000,
                        help="Total swap txs to send (default: 1000)")
    parser.add_argument("--gas", type=int, default=DEFAULT_GAS_SWAP,
                        help=f"Gas limit per swap (default: {DEFAULT_GAS_SWAP:,})")
    parser.add_argument("--dry-run", action="store_true", help="Build and sign but don't send")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    # Résoudre la liste de wallets
    if args.wallets_dir:
        pem_files = sorted(Path(args.wallets_dir).expanduser().glob("*.pem"))
        if not pem_files:
            print(f"[ERROR] No .pem files found in {args.wallets_dir}", file=sys.stderr)
            sys.exit(1)
        wallets = [str(p) for p in pem_files]
    else:
        wallets = args.wallets

    wallets = wallets[:args.max_wallets]

    provider = utils.get_provider(args.gateway)

    amount_in_raw = int(args.amount_in * (10 ** args.decimals))
    data_str = build_swap_data(args.token_in, amount_in_raw, args.token_out, args.min_out)

    print(f"[INFO] Pool:      {DEX_POOL}")
    print(f"[INFO] Token In:  {args.token_in} | {args.amount_in} units = {amount_in_raw} raw")
    print(f"[INFO] Token Out: {args.token_out} | min_out={args.min_out}")
    print(f"[INFO] Data:      {data_str[:100]}{'...' if len(data_str) > 100 else ''}")
    print(f"[INFO] Wallets:   {len(wallets)} | Target: {args.target} swaps | Gas: {args.gas:,}")
    if args.dry_run:
        print("[INFO] DRY-RUN – transactions will NOT be sent\n")

    # Distribute swaps across wallets
    n = len(wallets)
    base, extra = divmod(args.target, n)
    txs_per_wallet = [base + (1 if i < extra else 0) for i in range(n)]

    counter = [0]
    lock = threading.Lock()
    start = time.time()

    threads = [
        threading.Thread(
            target=wallet_worker,
            args=(pem, txs, data_str, args.gas, provider, args.dry_run, counter, lock),
            daemon=True,
        )
        for pem, txs in zip(wallets, txs_per_wallet)
        if txs > 0
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    elapsed = time.time() - start
    total = counter[0]
    rate = total / elapsed if elapsed > 0 else 0
    print(f"\n[DONE] {total} swap transactions in {elapsed:.1f}s ({rate:.1f} tx/s)")


if __name__ == "__main__":
    main()
