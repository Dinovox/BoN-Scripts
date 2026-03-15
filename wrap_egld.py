"""
Wrapper EGLD → WEGLD sur le réseau BoN.

Envoie de l'EGLD au smart contract de wrapping avec data=`wrapEgld`.
Le sender doit être sur le même shard que le wrap SC (shard 1 par défaut).

Usage:
  python wrap_egld.py --wallet ./wallets/bon_supernova.pem --amount 10.0
  python wrap_egld.py --wallet ./wallets/bon_supernova.pem --amount 10.0 --dry-run

Options:
  --wrap-sc BECH32    Contrat de wrapping (défaut: config.WEGLD_WRAP_SC_SHARD1)

  python wrap_egld.py --wallet ./wallets/bon_supernova.pem --amount 10.0 --dry-run

"""
import argparse
import json

from multiversx_sdk import Address, Transaction, TransactionComputer

import config
import utils

EGLD_DENOMINATION = 10 ** 18
GAS_WRAP = 3_000_000


def main():
    parser = argparse.ArgumentParser(description="Wrapper EGLD → WEGLD sur BoN")
    parser.add_argument("--wallet", default="./wallets/bon_supernova.pem",
                        help="Wallet PEM (défaut: ./wallets/bon_supernova.pem)")
    parser.add_argument("--amount", type=float, required=True,
                        help="Montant EGLD à wrapper")
    parser.add_argument("--wrap-sc", default=config.WEGLD_WRAP_SC_SHARD1,
                        help="Adresse du smart contract de wrapping")
    parser.add_argument("--gas", type=int, default=GAS_WRAP,
                        help=f"Gas limit (défaut: {GAS_WRAP:,})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Afficher la tx sans l'envoyer")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    provider = utils.get_provider(args.gateway)
    signer = utils.load_signer(args.wallet)
    sender = utils.get_address(signer)
    computer = TransactionComputer()

    value_raw = int(args.amount * EGLD_DENOMINATION)
    nonce = utils.get_account_nonce(provider, sender)

    print(f"[INFO] Sender:   {sender.to_bech32()}")
    print(f"[INFO] Wrap SC:  {args.wrap_sc}")
    print(f"[INFO] Amount:   {args.amount} EGLD ({value_raw} raw)")
    print(f"[INFO] Gas:      {args.gas:,}")
    print(f"[INFO] Nonce:    {nonce}")
    if args.dry_run:
        print("[INFO] DRY-RUN – transaction will NOT be sent\n")

    tx = Transaction(
        nonce=nonce,
        sender=sender,
        receiver=Address.new_from_bech32(args.wrap_sc),
        value=value_raw,
        gas_limit=args.gas,
        data=b"wrapEgld",
        chain_id=config.CHAIN_ID,
        gas_price=config.DEFAULT_GAS_PRICE,
        version=config.DEFAULT_TX_VERSION,
    )
    tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))

    if args.dry_run:
        tx_dict = {
            "nonce": tx.nonce,
            "sender": tx.sender.to_bech32(),
            "receiver": tx.receiver.to_bech32(),
            "value": str(tx.value),
            "gasLimit": tx.gas_limit,
            "data": "wrapEgld",
            "chainID": tx.chain_id,
            "signature": tx.signature.hex(),
        }
        print("[DRY-RUN] Transaction (non envoyée) :")
        print(json.dumps(tx_dict, indent=2))
        return

    tx_hash = provider.send_transaction(tx)
    if isinstance(tx_hash, bytes):
        tx_hash = tx_hash.hex()
    print(f"[OK] Transaction envoyée : {tx_hash}")
    print(f"     Explorer : https://bon-explorer.multiversx.com/transactions/{tx_hash}")


if __name__ == "__main__":
    main()
