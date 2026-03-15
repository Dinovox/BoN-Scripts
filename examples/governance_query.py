"""
Exemple : lire une proposal de gouvernance en cours sur BoN.

Usage :
  python examples/governance_query.py [--proposal 4]
"""
import argparse
import sys
import os

# Permet d'importer les modules du dossier parent
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from multiversx_sdk import ProxyNetworkProvider, Address, SmartContractQuery
import config
import utils


def query_proposal(proposal_nonce: int):
    provider = utils.get_provider()
    contract = Address.new_from_bech32(config.GOVERNANCE_SC)

    nonce_hex = utils.encode_biguint(proposal_nonce)
    print(f"[INFO] Query getProposal({proposal_nonce}) sur {config.GOVERNANCE_SC}")

    query = SmartContractQuery(
        contract=contract,
        function="getProposal",
        arguments=[bytes.fromhex(nonce_hex)] if nonce_hex else [b""],
    )

    response = provider.query_contract(query)

    if response.return_code != "ok":
        print(f"[ERROR] {response.return_code}: {response.return_message}")
        return

    print(f"[INFO] return_code: {response.return_code}")
    for i, val in enumerate(response.return_data):
        decoded = utils.decode_return_value(val)
        parts = []
        if decoded.get("hex"):
            parts.append(f"hex={decoded['hex']}")
        if "int" in decoded:
            parts.append(f"int={decoded['int']}")
        if "str" in decoded:
            parts.append(f'str="{decoded["str"]}"')
        print(f"  [{i}] {' | '.join(parts) or '(vide)'}")


def query_governance_config():
    """Affiche la config du SC gouvernance (liste les paramètres globaux)."""
    provider = utils.get_provider()
    contract = Address.new_from_bech32(config.GOVERNANCE_SC)

    print(f"[INFO] Query getContractConfig sur {config.GOVERNANCE_SC}")

    query = SmartContractQuery(
        contract=contract,
        function="getContractConfig",
        arguments=[],
    )
    response = provider.query_contract(query)
    print(f"[INFO] return_code: {response.return_code}")
    for i, val in enumerate(response.return_data):
        decoded = utils.decode_return_value(val)
        print(f"  [{i}] hex={decoded.get('hex', '')} int={decoded.get('int', '')} str={decoded.get('str', '')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lire une proposal governance BoN")
    parser.add_argument("--proposal", type=int, default=4, help="Nonce de la proposal (défaut: 4)")
    parser.add_argument("--config", action="store_true", dest="show_config", help="Afficher la config du SC")
    args = parser.parse_args()

    if args.show_config:
        query_governance_config()
    else:
        query_proposal(args.proposal)
