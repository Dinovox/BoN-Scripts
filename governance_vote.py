"""
Voter sur une proposal de gouvernance BoN.

Usage :
  python governance_vote.py --wallet <pem> [--proposal 4] [--vote yes] [--dry-run]

Exemples :
  python governance_vote.py --wallet ~/wallet.pem --proposal 4 --vote yes --dry-run
  python governance_vote.py --wallet ~/wallet.pem --proposal 4 --vote yes
"""
import argparse
import json
import sys

from multiversx_sdk import Transaction, TransactionComputer, Address

import config
import utils

# Encodage VoteType (enum MultiversX governance SC)
VOTE_TYPES = {
    "yes":     "00",
    "no":      "01",
    "veto":    "02",
    "abstain": "03",
}

GOVERNANCE_GAS_LIMIT = 6_000_000


def build_vote_data(proposal_nonce: int, vote: str) -> str:
    """
    Construit la data field pour un vote governance.
    Exemple : vote@04@00  (nonce=4, Yes)
    """
    nonce_hex = utils.encode_biguint(proposal_nonce)
    vote_hex = VOTE_TYPES[vote]
    return f"vote@{nonce_hex}@{vote_hex}"


def main():
    parser = argparse.ArgumentParser(description="Voter sur une governance proposal BoN")
    parser.add_argument("--wallet", required=True, help="Chemin vers le wallet PEM (ed25519)")
    parser.add_argument("--proposal", type=int, default=4, help="Nonce de la proposal (défaut: 4)")
    parser.add_argument(
        "--vote",
        choices=list(VOTE_TYPES.keys()),
        default="yes",
        help="Type de vote (défaut: yes)",
    )
    parser.add_argument("--sc", default=config.GOVERNANCE_SC, help="Adresse du Governance SC")
    parser.add_argument("--gas", type=int, default=GOVERNANCE_GAS_LIMIT, help="Gas limit")
    parser.add_argument("--gateway", default=config.GATEWAY_URL, help="URL du gateway")
    parser.add_argument("--nonce", type=int, default=None, help="Forcer le nonce du compte (optionnel)")
    parser.add_argument("--dry-run", action="store_true", help="Afficher la tx sans l'envoyer")
    args = parser.parse_args()

    data = build_vote_data(args.proposal, args.vote)

    print(f"[INFO] Proposal nonce : {args.proposal}")
    print(f"[INFO] Vote           : {args.vote.upper()}  (data: {data})")
    print(f"[INFO] SC Governance  : {args.sc}")

    provider = utils.get_provider(args.gateway)
    signer = utils.load_signer(args.wallet)
    sender_address = utils.get_address(signer)

    print(f"[INFO] Adresse sender : {sender_address.to_bech32()}")

    nonce = args.nonce if args.nonce is not None else utils.get_account_nonce(provider, sender_address)

    tx = Transaction(
        nonce=nonce,
        sender=sender_address.to_bech32(),
        receiver=args.sc,
        value=0,
        gas_limit=args.gas,
        data=data.encode(),
        chain_id=config.CHAIN_ID,
        gas_price=config.DEFAULT_GAS_PRICE,
        version=config.DEFAULT_TX_VERSION,
    )

    computer = TransactionComputer()
    tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))

    if args.dry_run:
        tx_dict = {
            "nonce": tx.nonce,
            "sender": tx.sender,
            "receiver": tx.receiver,
            "value": str(tx.value),
            "gasLimit": tx.gas_limit,
            "data": data,
            "chainID": tx.chain_id,
            "signature": tx.signature.hex(),
        }
        print("\n[DRY-RUN] Transaction (non envoyée) :")
        print(json.dumps(tx_dict, indent=2))
        return

    tx_hash = provider.send_transaction(tx)
    print(f"\n[OK] Vote soumis ! txHash : {tx_hash}")
    print(f"     Explorer : https://bon.multiversx.com/transactions/{tx_hash}")


if __name__ == "__main__":
    main()
