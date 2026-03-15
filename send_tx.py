"""
Envoyer une transaction signée sur le réseau BoN.

Usage :
  python send_tx.py --wallet <pem> --to <bech32> --data <str> [options]

Exemples :
  # Vote governance (dry-run)
  python send_tx.py --wallet ~/wallet.pem --to erd1qqq...ylll --data "vote@04@00" --gas 6000000 --dry-run

  # Transfer EGLD
  python send_tx.py --wallet ~/wallet.pem --to erd1abc... --value 0.1

  # Call SC sans valeur
  python send_tx.py --wallet ~/wallet.pem --to erd1qqq... --data "claimRewards" --gas 10000000

  #send 0.5 EGLD à un wallet
    python send_tx.py --wallet ./wallets/bon_guilds.pem \
    --to erd18yn5z97gcx2552364arpse4n495ryps3ccm0d5wq4k3p3e6pfvpqx6st89 \
    --value 0.5 \
    --dry-run
"""
import argparse
import json
import sys

from multiversx_sdk import Address, Transaction, TransactionComputer

import config
import utils


EGLD_DENOMINATION = 10 ** 18


def build_and_send(
    wallet_pem: str,
    receiver_bech32: str,
    data_str: str,
    value_egld: float,
    gas_limit: int,
    nonce_override: int | None,
    gateway_url: str,
    dry_run: bool,
) -> str | None:
    provider = utils.get_provider(gateway_url)
    signer = utils.load_signer(wallet_pem)
    sender_address = utils.get_address(signer)

    nonce = nonce_override if nonce_override is not None else utils.get_account_nonce(provider, sender_address)
    value_raw = int(value_egld * EGLD_DENOMINATION)

    tx = Transaction(
        nonce=nonce,
        sender=sender_address,
        receiver=Address.new_from_bech32(receiver_bech32),
        value=value_raw,
        gas_limit=gas_limit,
        data=data_str.encode() if data_str else b"",
        chain_id=config.CHAIN_ID,
        gas_price=config.DEFAULT_GAS_PRICE,
        version=config.DEFAULT_TX_VERSION,
    )

    computer = TransactionComputer()
    tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))

    if dry_run:
        tx_dict = {
            "nonce": tx.nonce,
            "sender": tx.sender.to_bech32(),
            "receiver": tx.receiver.to_bech32(),
            "value": str(tx.value),
            "gasLimit": tx.gas_limit,
            "gasPrice": tx.gas_price,
            "data": data_str,
            "chainID": tx.chain_id,
            "version": tx.version,
            "signature": tx.signature.hex(),
        }
        print("[DRY-RUN] Transaction (non envoyée) :")
        print(json.dumps(tx_dict, indent=2))
        return None

    tx_hash = provider.send_transaction(tx)
    if isinstance(tx_hash, bytes):
        tx_hash = tx_hash.hex()
    print(f"[OK] Transaction envoyée : {tx_hash}")
    print(f"     Explorer : https://bon-explorer.multiversx.com/transactions/{tx_hash}")
    return tx_hash


def main():
    parser = argparse.ArgumentParser(description="Envoyer une transaction sur BoN")
    parser.add_argument("--wallet", required=True, help="Chemin vers le wallet PEM (ed25519)")
    parser.add_argument("--to", required=True, dest="receiver", help="Adresse destinataire (bech32)")
    parser.add_argument("--data", default="", help="Data field en clair (ex: 'vote@04@00')")
    parser.add_argument("--value", type=float, default=0.0, help="Valeur en EGLD (défaut: 0)")
    parser.add_argument("--gas", type=int, default=50_000, help="Gas limit (défaut: 50000)")
    parser.add_argument("--nonce", type=int, default=None, help="Forcer le nonce (optionnel)")
    parser.add_argument("--gateway", default=config.GATEWAY_URL, help="URL du gateway")
    parser.add_argument("--dry-run", action="store_true", help="Afficher la tx sans l'envoyer")
    args = parser.parse_args()

    build_and_send(
        wallet_pem=args.wallet,
        receiver_bech32=args.receiver,
        data_str=args.data,
        value_egld=args.value,
        gas_limit=args.gas,
        nonce_override=args.nonce,
        gateway_url=args.gateway,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
