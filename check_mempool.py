"""
Affiche les transactions en attente dans le mempool pour une adresse donnée.

Interroge le node API direct (port 808X) via :
  GET http://{host}:{8080+shard}/transaction/pool?by-sender={address}

Usage :
  # Shard auto-détecté, node direct
  python check_mempool.py --address erd1xxxxx...

  # Via gateway proxy (fallback)
  python check_mempool.py --address erd1xxxxx... --gateway http://localhost:8079

  # Interroge les 3 shards (debug / si shard incertain)
  python check_mempool.py --address erd1xxxxx... --all-shards

  # Depuis un fichier PEM
  python check_mempool.py --pem ./spam-wallets/shard-0/wallet_014.pem
"""
import argparse
import sys

import requests
from multiversx_sdk import Address, AddressComputer

import config
import utils

NUM_SHARDS        = config.POOL_NUM_SHARDS   # 3
NODE_HOST         = config.NODE_HOST
NODE_BASE_PORT    = config.NODE_BASE_PORT

_address_computer = AddressComputer(number_of_shards=NUM_SHARDS)


def get_shard(address: Address) -> int:
    return _address_computer.get_shard_of_address(address)


def node_url(shard: int) -> str:
    return f"http://{NODE_HOST}:{NODE_BASE_PORT + shard}"


def fetch_pool(base_url: str, bech32: str, timeout: int = 10) -> list[dict]:
    """
    Retourne la liste des txs en attente pour ce sender.
    Endpoint : /transaction/pool?by-sender={bech32}
    """
    url = (f"{base_url.rstrip('/')}/transaction/pool"
           f"?by-sender={bech32}"
           f"&fields=hash,nonce,sender,receiver,value,gasprice,gaslimit")
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    inner   = data.get("data", {})
    tx_pool = inner.get("txPool", inner) if isinstance(inner, dict) else {}

    txs = []
    for key in ("regularTransactions", "transactions"):
        chunk = tx_pool.get(key, [])
        if isinstance(chunk, dict):
            chunk = list(chunk.values())
        for item in chunk:
            # gateway wraps les champs sous "txFields"
            txs.append(item.get("txFields", item))

    return txs if txs else (inner if isinstance(inner, list) else [])


def display_pool(txs: list[dict], bech32: str, source_url: str, shard: int | None):
    shard_str = f"shard {shard}" if shard is not None else "gateway"
    print(f"\n[INFO] {bech32}")
    print(f"       {shard_str} | {source_url}")

    if not txs:
        print("       0 txs waiting in pool\n")
        return

    print(f"       {len(txs)} txs in pool\n")

    # Trier par nonce
    try:
        txs_sorted = sorted(txs, key=lambda t: int(t.get("nonce", 0)))
    except Exception:
        txs_sorted = txs

    col_n  = 10
    col_v  = 14
    col_r  = 20
    col_gp = 14
    print(f"  {'nonce':>{col_n}}  {'value (EGLD)':>{col_v}}  {'receiver':<{col_r}}  {'gasPrice':>{col_gp}}")
    print(f"  {'-'*col_n}  {'-'*col_v}  {'-'*col_r}  {'-'*col_gp}")

    for tx in txs_sorted:
        nonce    = tx.get("nonce", "?")
        value    = tx.get("value", "0")
        receiver = tx.get("receiver", "?")
        gp       = tx.get("gasprice", "?")
        try:
            egld = int(value) / 1e18
            val_str = f"{egld:.5f}"
        except Exception:
            val_str = str(value)
        recv_short = receiver[:col_r] if isinstance(receiver, str) else str(receiver)
        print(f"  {str(nonce):>{col_n}}  {val_str:>{col_v}}  {recv_short:<{col_r}}  {str(gp):>{col_gp}}")

    # Analyse des gaps de nonce
    nonces = []
    for tx in txs_sorted:
        try:
            nonces.append(int(tx.get("nonce", -1)))
        except Exception:
            pass

    if len(nonces) >= 2:
        nonces_set = set(nonces)
        expected   = set(range(min(nonces), max(nonces) + 1))
        gaps       = sorted(expected - nonces_set)
        print(f"\n  nonce range : {min(nonces)} → {max(nonces)}", end="")
        if gaps:
            print(f"\n  [WARN] {len(gaps)} gap(s) détecté(s) : {gaps[:10]}{'...' if len(gaps) > 10 else ''}")
        else:
            print("  (aucun gap)")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Vérifier les txs en attente dans le mempool pour une adresse"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--address", metavar="ERD1",
                       help="Adresse bech32 (erd1...)")
    group.add_argument("--pem", metavar="FILE",
                       help="Fichier PEM du wallet")

    parser.add_argument("--gateway", default=config.GATEWAY_URL,
                        help=f"Gateway à interroger (défaut: {config.GATEWAY_URL})")
    parser.add_argument("--node", action="store_true",
                        help="Utiliser les nodes directs (localhost:808X) au lieu du gateway")
    parser.add_argument("--all-shards", action="store_true",
                        help="Interroger les 3 shards node directs (debug, implique --node)")
    parser.add_argument("--node-host", default=NODE_HOST,
                        help=f"Hôte des nodes directs (défaut: {NODE_HOST})")
    parser.add_argument("--node-port", type=int, default=NODE_BASE_PORT,
                        help=f"Port de base shard 0 (défaut: {NODE_BASE_PORT})")
    args = parser.parse_args()

    # Résoudre l'adresse
    if args.pem:
        signer  = utils.load_signer(args.pem)
        address = utils.get_address(signer)
    else:
        try:
            address = Address.new_from_bech32(args.address)
        except Exception:
            print(f"[ERROR] Adresse invalide : {args.address}", file=sys.stderr)
            sys.exit(1)

    bech32 = address.to_bech32()
    shard  = get_shard(address)

    # Mode node direct
    if args.node or args.all_shards:
        host = args.node_host
        base = args.node_port
        shards_to_query = range(NUM_SHARDS) if args.all_shards else [shard]
        for s in shards_to_query:
            url = f"http://{host}:{base + s}"
            label = f"shard {s} → {url}"
            if not args.all_shards:
                label = f"shard {s} auto-détecté → {url}"
            print(f"[INFO] {label} ...", end=" ", flush=True)
            try:
                txs = fetch_pool(url, bech32)
                print(f"{len(txs)} txs")
                display_pool(txs, bech32, url, s)
            except Exception as e:
                print(f"ERREUR ({e})")
        return

    # Mode gateway (défaut)
    gw = args.gateway
    print(f"[INFO] gateway : {gw}")
    try:
        txs = fetch_pool(gw, bech32)
        display_pool(txs, bech32, gw, shard)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
