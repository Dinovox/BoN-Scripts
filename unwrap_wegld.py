"""
unwrap_wegld.py — Unwrap WEGLD → EGLD pour chaque wallet.

Envoie ESDTTransfer(WEGLD, balance, unwrapEgld) vers le wrap SC du même shard.
Chaque wallet unwrap toute sa balance WEGLD d'un coup.

Usage :
  python unwrap_wegld.py \\
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
from multiversx_sdk import Address, AddressComputer, Transaction, TransactionComputer

import config
import utils

WEGLD_TOKEN_ID = "WEGLD-bd4d79"
GAS_UNWRAP     = 3_000_000

WEGLD_WRAP_SC = {
    0: "erd1qqqqqqqqqqqqqpgqvc7gdl0p4s97guh498wgz75k8sav6sjfjlwqh679jy",
    1: "erd1qqqqqqqqqqqqqpgqhe8t5jewej70zupmh44jurgn29psua5l2jps3ntjj3",
    2: "erd1qqqqqqqqqqqqqpgqmuk0q2saj0mgutxm4teywre6dl8wqf58xamqdrukln",
}

NUM_SHARDS        = 3
_addr_computer    = AddressComputer(number_of_shards=NUM_SHARDS)


def compute_shard(address) -> int:
    return _addr_computer.get_shard_of_address(address)


def encode_hex(value: str) -> str:
    return value.encode().hex()


def encode_amount_hex(amount: int) -> str:
    if amount == 0:
        return "00"
    h = hex(amount)[2:]
    return h if len(h) % 2 == 0 else "0" + h


def build_unwrap_data(amount: int) -> bytes:
    """ESDTTransfer@<WEGLD_hex>@<amount_hex>@<unwrapEgld_hex>"""
    return "@".join([
        "ESDTTransfer",
        encode_hex(WEGLD_TOKEN_ID),
        encode_amount_hex(amount),
        encode_hex("unwrapEgld"),
    ]).encode()


def get_wegld_balance(gateway_url: str, address: Address) -> int:
    url = f"{gateway_url.rstrip('/')}/address/{address.to_bech32()}/esdt"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        esdts = r.json().get("data", {}).get("esdts", {})
        return int(esdts.get(WEGLD_TOKEN_ID, {}).get("balance", 0))
    except Exception:
        return 0


def get_nonce(gateway_url: str, address: Address) -> int:
    url = f"{gateway_url.rstrip('/')}/address/{address.to_bech32()}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return int(r.json().get("data", {}).get("account", {}).get("nonce", 0))


def main():
    parser = argparse.ArgumentParser(
        description="Unwrap WEGLD → EGLD pour chaque wallet"
    )
    parser.add_argument("--wallets-dir", action="append", dest="wallets_dirs", required=True,
                        help="Dossier(s) contenant les PEM (peut être répété)")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    parser.add_argument("--dry-run", action="store_true",
                        help="Afficher les txs sans les envoyer")
    args = parser.parse_args()

    computer = TransactionComputer()
    provider = utils.get_provider(args.gateway)

    pem_files = []
    for d in args.wallets_dirs:
        pem_files.extend(sorted(Path(d).expanduser().glob("*.pem")))

    if not pem_files:
        print("[ERROR] Aucun PEM trouvé", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] {len(pem_files)} wallets à traiter")
    unwrapped = 0
    skipped   = 0

    for pem in pem_files:
        signer  = utils.load_signer(str(pem))
        address = utils.get_address(signer)
        shard   = compute_shard(address)

        wrap_sc_addr_str = WEGLD_WRAP_SC.get(shard)
        if wrap_sc_addr_str is None:
            print(f"[SKIP] {pem.name} — shard {shard} inconnu")
            skipped += 1
            continue

        wegld_bal = get_wegld_balance(args.gateway, address)
        if wegld_bal == 0:
            print(f"[SKIP] {pem.name} (shard {shard}) — pas de WEGLD")
            skipped += 1
            continue

        try:
            nonce = get_nonce(args.gateway, address)
        except Exception as e:
            print(f"[ERROR] {pem.name}: nonce fetch: {e}")
            continue

        wrap_sc = Address.new_from_bech32(wrap_sc_addr_str)
        tx = Transaction(
            nonce=nonce,
            sender=address,
            receiver=wrap_sc,
            value=0,
            gas_limit=GAS_UNWRAP,
            data=build_unwrap_data(wegld_bal),
            chain_id=config.CHAIN_ID,
            gas_price=config.DEFAULT_GAS_PRICE,
            version=config.DEFAULT_TX_VERSION,
        )
        tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))

        if args.dry_run:
            print(f"[DRY-RUN] {pem.name} (shard {shard}) : "
                  f"unwrap {wegld_bal/1e18:.6f} WEGLD → EGLD (nonce={nonce})")
            unwrapped += 1
            continue

        try:
            tx_hash = provider.send_transaction(tx)
            print(f"[UNWRAP] {pem.name} (shard {shard}) : "
                  f"{wegld_bal/1e18:.6f} WEGLD → EGLD → {tx_hash}")
            unwrapped += 1
        except Exception as e:
            print(f"[ERROR] {pem.name}: {e}")
        time.sleep(0.2)

    print(f"\n[DONE] {unwrapped} unwraps envoyés, {skipped} wallets sans WEGLD")


if __name__ == "__main__":
    main()
