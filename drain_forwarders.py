"""
drain_forwarders.py — Drain WEGLD + USDC des contrats forwarder déployés.

Envoie drain@<token_hex>@00 pour chaque token depuis le wallet déployeur (owner)
de chaque forwarder. À utiliser avant/après la fenêtre de challenge pour récupérer
les tokens bloqués dans les forwarders suite aux appels cross-shard.

Usage :
  python drain_forwarders.py --forwarders forwarders.json --wallets-dir ./spam-wallets

  # Forcer un wallet déployeur spécifique (si forwarders.json n'a pas deployer_pem) :
  python drain_forwarders.py --forwarders forwarders.json \\
      --deployer-shard0 ./deploy-wallets/shard-0/wallet_000.pem \\
      --deployer-shard1 ./deploy-wallets/shard-1/wallet_000.pem \\
      --deployer-shard2 ./deploy-wallets/shard-2/wallet_000.pem
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests
from multiversx_sdk import Address, Transaction, TransactionComputer

import config
import utils


WEGLD_TOKEN_ID = "WEGLD-bd4d79"
USDC_TOKEN_ID  = "USDC-c76f1f"
GAS_DRAIN      = 10_000_000


def encode_hex(value: str) -> str:
    return value.encode().hex()


def build_drain_data(token_id: str) -> bytes:
    return f"drain@{encode_hex(token_id)}@00".encode()


def get_account_nonce_balance(gateway_url: str, address: Address) -> tuple[int, int]:
    url = f"{gateway_url.rstrip('/')}/address/{address.to_bech32()}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json().get("data", {}).get("account", {})
    return int(data.get("nonce", 0)), int(data.get("balance", 0))


def drain_forwarder(shard: int, forwarder_addr: Address, deployer_pem: str,
                    gateway_url: str, provider, dry_run: bool):
    signer   = utils.load_signer(deployer_pem)
    address  = utils.get_address(signer)
    computer = TransactionComputer()

    print(f"[shard-{shard}] deployer  : {address.to_bech32()}")
    print(f"[shard-{shard}] forwarder : {forwarder_addr.to_bech32()}")

    try:
        nonce, balance = get_account_nonce_balance(gateway_url, address)
    except Exception as e:
        print(f"[shard-{shard}] [ERROR] Impossible de récupérer le compte: {e}")
        return

    gas_cost = GAS_DRAIN * config.DEFAULT_GAS_PRICE
    if balance < gas_cost * 2:
        print(f"[shard-{shard}] [WARN] Balance insuffisante pour 2 drain txs "
              f"({balance / 1e18:.4f} EGLD)")

    txs = []
    for token_id in [WEGLD_TOKEN_ID, USDC_TOKEN_ID]:
        tx = Transaction(
            nonce=nonce + len(txs),
            sender=address,
            receiver=forwarder_addr,
            value=0,
            gas_limit=GAS_DRAIN,
            data=build_drain_data(token_id),
            chain_id=config.CHAIN_ID,
            gas_price=config.DEFAULT_GAS_PRICE,
            version=config.DEFAULT_TX_VERSION,
        )
        tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
        txs.append((token_id, tx))

    if dry_run:
        for token_id, tx in txs:
            print(f"[DRY-RUN][shard-{shard}] drain {token_id} nonce={tx.nonce} "
                  f"data={tx.data.decode()}")
        return

    for token_id, tx in txs:
        try:
            tx_hash = provider.send_transaction(tx)
            print(f"[shard-{shard}] drain {token_id} → {tx_hash}")
        except Exception as e:
            print(f"[shard-{shard}] [ERROR] drain {token_id}: {e}")
        time.sleep(0.3)


def find_deployer_pem(wallets_dir: str, shard: int) -> str | None:
    """Cherche le premier PEM dans wallets_dir/shard-X/."""
    shard_dir = Path(wallets_dir) / f"shard-{shard}"
    if not shard_dir.exists():
        return None
    pems = sorted(shard_dir.glob("*.pem"))
    return str(pems[0]) if pems else None


def main():
    parser = argparse.ArgumentParser(
        description="Drain WEGLD + USDC des forwarder contracts déployés"
    )
    parser.add_argument("--forwarders", default="forwarders.json",
                        help="JSON des forwarder contracts (défaut: forwarders.json)")
    parser.add_argument("--wallets-dir", default=None, dest="wallets_dir",
                        help="Dossier base des wallets (cherche shard-X/ dedans)")
    parser.add_argument("--deployer-shard0", default=None, dest="deployer_shard0",
                        metavar="PEM", help="PEM déployeur shard 0 (override forwarders.json)")
    parser.add_argument("--deployer-shard1", default=None, dest="deployer_shard1",
                        metavar="PEM", help="PEM déployeur shard 1 (override forwarders.json)")
    parser.add_argument("--deployer-shard2", default=None, dest="deployer_shard2",
                        metavar="PEM", help="PEM déployeur shard 2 (override forwarders.json)")
    parser.add_argument("--shards", nargs="+", type=int, default=[0, 1, 2],
                        help="Shards à drainer (défaut: 0 1 2)")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    parser.add_argument("--dry-run", action="store_true",
                        help="Afficher les txs sans les envoyer")
    args = parser.parse_args()

    # Chargement du JSON
    try:
        with open(args.forwarders) as f:
            raw = json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] {args.forwarders} introuvable. Déploie d'abord les forwarders.",
              file=sys.stderr)
        sys.exit(1)

    provider = utils.get_provider(args.gateway)

    # Override PEMs par CLI
    cli_overrides = {
        0: args.deployer_shard0,
        1: args.deployer_shard1,
        2: args.deployer_shard2,
    }

    for shard in args.shards:
        key = f"shard-{shard}"
        if key not in raw:
            print(f"[WARN] shard-{shard} absent de {args.forwarders} — ignoré")
            continue

        info            = raw[key]
        forwarder_addr  = Address.new_from_bech32(info["contract"])

        # Résolution du PEM déployeur : CLI > forwarders.json > wallets_dir
        deployer_pem = (
            cli_overrides.get(shard)
            or info.get("deployer_pem")
            or (find_deployer_pem(args.wallets_dir, shard) if args.wallets_dir else None)
        )

        if not deployer_pem:
            print(f"[ERROR] shard-{shard}: aucun PEM déployeur trouvé. "
                  f"Utilise --deployer-shard{shard} ou --wallets-dir.", file=sys.stderr)
            continue

        if args.dry_run:
            print(f"\n--- [DRY-RUN] shard-{shard} ---")
        else:
            print(f"\n--- shard-{shard} ---")

        drain_forwarder(shard, forwarder_addr, deployer_pem,
                        args.gateway, provider, args.dry_run)

    print("\nDrain terminé.")


if __name__ == "__main__":
    main()
