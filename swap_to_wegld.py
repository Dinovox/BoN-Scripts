"""
swap_to_wegld.py — Swap USDC → WEGLD pour chaque wallet (cleanup post-challenge).

Pour chaque wallet, vérifie la balance USDC et envoie 1 ESDTTransfer direct
vers le DEX pair SC (swapTokensFixedInput WEGLD, min_out=1).
Ne passe pas par le forwarder — appel DEX direct.

Usage :
  python swap_to_wegld.py \\
    --wallets-dir ./scen6-wallets/shard-0 \\
    --wallets-dir ./scen6-wallets/shard-1 \\
    --wallets-dir ./scen6-wallets/shard-2 \\
    --wallets-dir ./deploy-wallets/shard-0 \\
    --wallets-dir ./deploy-wallets/shard-1 \\
    --wallets-dir ./deploy-wallets/shard-2 \\
    --dry-run
"""
import argparse
import sys
import time
from pathlib import Path

import requests
from multiversx_sdk import Address, Transaction, TransactionComputer

import config
import utils

WEGLD_TOKEN_ID = "WEGLD-bd4d79"
USDC_TOKEN_ID  = "USDC-c76f1f"
GAS_SWAP       = 15_000_000
DEX_PAIR_SC    = "erd1qqqqqqqqqqqqqpgqeel2kumf0r8ffyhth7pqdujjat9nx0862jpsg2pqaq"


def encode_hex(value: str) -> str:
    return value.encode().hex()


def encode_amount_hex(amount: int) -> str:
    if amount == 0:
        return "00"
    h = hex(amount)[2:]
    return h if len(h) % 2 == 0 else "0" + h


def build_swap_data(token_in: str, amount_in: int, token_out: str, min_out: int = 1) -> bytes:
    data = "@".join([
        "ESDTTransfer",
        encode_hex(token_in),
        encode_amount_hex(amount_in),
        encode_hex("swapTokensFixedInput"),
        encode_hex(token_out),
        encode_amount_hex(min_out),
    ])
    return data.encode()


def get_esdt_balance(gateway_url: str, address: Address, token_id: str) -> int:
    url = f"{gateway_url.rstrip('/')}/address/{address.to_bech32()}/esdt"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        esdts = r.json().get("data", {}).get("esdts", {})
        return int(esdts.get(token_id, {}).get("balance", 0))
    except Exception:
        return 0


def get_nonce(gateway_url: str, address: Address) -> int:
    url = f"{gateway_url.rstrip('/')}/address/{address.to_bech32()}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return int(r.json().get("data", {}).get("account", {}).get("nonce", 0))


def main():
    parser = argparse.ArgumentParser(
        description="Swap USDC → WEGLD direct DEX pour chaque wallet (cleanup post-challenge)"
    )
    parser.add_argument("--wallets-dir", action="append", dest="wallets_dirs", required=True,
                        help="Dossier(s) contenant les PEM (peut être répété)")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    parser.add_argument("--dry-run", action="store_true",
                        help="Afficher les txs sans les envoyer")
    args = parser.parse_args()

    dex_addr = Address.new_from_bech32(DEX_PAIR_SC)
    computer = TransactionComputer()
    provider = utils.get_provider(args.gateway)

    pem_files = []
    for d in args.wallets_dirs:
        pem_files.extend(sorted(Path(d).expanduser().glob("*.pem")))

    if not pem_files:
        print("[ERROR] Aucun PEM trouvé", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] {len(pem_files)} wallets à traiter")
    swapped = 0
    skipped = 0

    for pem in pem_files:
        signer  = utils.load_signer(str(pem))
        address = utils.get_address(signer)

        usdc_bal = get_esdt_balance(args.gateway, address, USDC_TOKEN_ID)
        if usdc_bal == 0:
            print(f"[SKIP] {pem.name} — pas de USDC")
            skipped += 1
            continue

        try:
            nonce = get_nonce(args.gateway, address)
        except Exception as e:
            print(f"[ERROR] {pem.name}: nonce fetch: {e}")
            continue

        tx = Transaction(
            nonce=nonce,
            sender=address,
            receiver=dex_addr,
            value=0,
            gas_limit=GAS_SWAP,
            data=build_swap_data(USDC_TOKEN_ID, usdc_bal, WEGLD_TOKEN_ID),
            chain_id=config.CHAIN_ID,
            gas_price=config.DEFAULT_GAS_PRICE,
            version=config.DEFAULT_TX_VERSION,
        )
        tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))

        if args.dry_run:
            print(f"[DRY-RUN] {pem.name} : swap {usdc_bal/1e6:.4f} USDC → WEGLD (nonce={nonce})")
            swapped += 1
            continue

        try:
            tx_hash = provider.send_transaction(tx)
            print(f"[SWAP] {pem.name} : {usdc_bal/1e6:.4f} USDC → WEGLD → {tx_hash}")
            swapped += 1
        except Exception as e:
            print(f"[ERROR] {pem.name}: {e}")
        time.sleep(0.2)

    print(f"\n[DONE] {swapped} swaps envoyés, {skipped} wallets sans USDC")


if __name__ == "__main__":
    main()
