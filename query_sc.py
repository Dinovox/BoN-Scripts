"""
Lire le résultat d'une vue (vmQuery) d'un smart contract BoN.

Usage :
  python query_sc.py --contract <bech32> --function <fn> [--args <hex>...]
  python query_sc.py --contract erd1qqq...ylll --function getProposal --args 04
  python query_sc.py --contract erd1qqq...ylll --function getContractConfig
"""
import argparse
import json
import sys

from multiversx_sdk import ProxyNetworkProvider, Address, SmartContractQuery

import config
import utils


def run_query(
    provider: ProxyNetworkProvider,
    contract_bech32: str,
    function: str,
    args_hex: list[str],
    caller: str | None = None,
) -> list[dict]:
    contract = Address.new_from_bech32(contract_bech32)
    arguments = [bytes.fromhex(a) for a in args_hex if a]

    query = SmartContractQuery(
        contract=contract,
        function=function,
        arguments=arguments,
        caller=Address.new_from_bech32(caller) if caller else None,
    )

    response = provider.query_contract(query)

    if response.return_code != "ok":
        print(f"[ERROR] vmQuery a échoué : {response.return_code} — {response.return_message}", file=sys.stderr)
        sys.exit(1)

    return [utils.decode_return_value(v) for v in response.return_data]


def main():
    parser = argparse.ArgumentParser(description="vmQuery : lire une vue d'un SC BoN")
    parser.add_argument("--contract", required=True, help="Adresse bech32 du SC")
    parser.add_argument("--function", required=True, help="Nom de la fonction vue")
    parser.add_argument("--args", nargs="*", default=[], metavar="HEX", help="Arguments en hex (ex: 04 0000000a)")
    parser.add_argument("--caller", default=None, help="Adresse appelante (optionnel, bech32)")
    parser.add_argument("--gateway", default=config.GATEWAY_URL, help="URL du gateway")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Sortie en JSON pur")
    args = parser.parse_args()

    provider = utils.get_provider(args.gateway)

    print(f"[INFO] Query  : {args.contract}::{args.function}({', '.join(args.args) or '—'})", file=sys.stderr)

    results = run_query(provider, args.contract, args.function, args.args, args.caller)

    if args.as_json:
        print(json.dumps(results, indent=2))
        return

    if not results:
        print("(aucune valeur de retour)")
        return

    for i, r in enumerate(results):
        parts = []
        if r.get("hex"):
            parts.append(f"hex={r['hex']}")
        if "int" in r:
            parts.append(f"int={r['int']}")
        if "str" in r:
            parts.append(f'str="{r["str"]}"')
        print(f"  [{i}] {' | '.join(parts)}")


if __name__ == "__main__":
    main()
