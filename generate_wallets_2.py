"""
Génère des wallets MultiversX et les trie par shard dans des sous-dossiers.

Identique à generate_wallets.py + mode --total N qui répartit N wallets
équitablement entre shards via divmod (ex: 500 → 167+167+166).

Structure de sortie :
  <output-dir>/
    shard-0/  wallet_000.pem  wallet_001.pem  …
    shard-1/  wallet_000.pem  …
    shard-2/  wallet_000.pem  …

Usage :
  # Répartition automatique équilibrée (recommandé)
  python generate_wallets_2.py --total 500 --output-dir ./windowa-wallets

  # S'arrêter quand chaque shard a 100 wallets
  python generate_wallets_2.py --target-per-shard 100 --output-dir ./wallets

  # Générer exactement 300 wallets, triés par shard (distribution non garantie)
  python generate_wallets_2.py --count 300 --output-dir ./wallets

  # Aperçu sans écrire de fichiers
  python generate_wallets_2.py --total 500 --num-shards 3 --dry-run
"""
import argparse
import base64
import sys
from pathlib import Path

from multiversx_sdk import AddressComputer, UserSecretKey


def write_pem(secret_key: UserSecretKey, output_path: Path) -> None:
    pubkey = secret_key.generate_public_key()
    address = pubkey.to_address(hrp="erd")
    bech32 = address.to_bech32()

    hex_content = (secret_key.get_bytes() + pubkey.get_bytes()).hex()
    b64 = base64.b64encode(hex_content.encode()).decode()
    b64_wrapped = "\n".join(b64[i : i + 64] for i in range(0, len(b64), 64))

    pem_text = (
        f"-----BEGIN PRIVATE KEY for {bech32}-----\n"
        f"{b64_wrapped}\n"
        f"-----END PRIVATE KEY for {bech32}-----\n"
    )
    output_path.write_text(pem_text)


def main():
    parser = argparse.ArgumentParser(description="Génère des wallets BoN triés par shard")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--count", type=int, default=None,
        help="Nombre total de wallets à générer (distribution non garantie par shard)",
    )
    group.add_argument(
        "--target-per-shard", type=int, default=None,
        help="S'arrête quand chaque shard a ce nombre de wallets (ex: 167)",
    )
    group.add_argument(
        "--total", type=int, default=None,
        help="Total exact réparti équitablement entre shards (ex: 500 → 167+167+166)",
    )
    parser.add_argument(
        "--output-dir", default="./wallets",
        help="Dossier de sortie (défaut: ./wallets)",
    )
    parser.add_argument(
        "--num-shards", type=int, default=3,
        help="Nombre de shards sur le réseau (défaut: 3)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Afficher les adresses et shards sans écrire de fichiers",
    )
    args = parser.parse_args()

    if args.count is None and args.target_per_shard is None and args.total is None:
        parser.error("Spécifiez --count, --target-per-shard ou --total")

    num_shards = args.num_shards
    address_computer = AddressComputer(number_of_shards=num_shards)

    counters: dict[int, int] = {s: 0 for s in range(num_shards)}
    output_dir = Path(args.output_dir).expanduser()

    # Cibles par shard
    if args.total is not None:
        base, extra = divmod(args.total, num_shards)
        targets: dict[int, int] = {s: base + (1 if s < extra else 0) for s in range(num_shards)}
    elif args.target_per_shard is not None:
        targets = {s: args.target_per_shard for s in range(num_shards)}
    else:
        targets = {}  # non utilisé en mode --count

    # Créer les sous-dossiers
    if not args.dry_run:
        for s in range(num_shards):
            (output_dir / f"shard-{s}").mkdir(parents=True, exist_ok=True)

    generated_total = 0

    def done() -> bool:
        if args.count is not None:
            return generated_total >= args.count
        return all(counters[s] >= targets[s] for s in range(num_shards))

    print(f"[INFO] Réseau: {num_shards} shards | Sortie: {output_dir}")
    if args.total is not None:
        detail = " + ".join(str(targets[s]) for s in range(num_shards))
        print(f"[INFO] Objectif: {args.total} wallets total ({detail})")
    elif args.target_per_shard:
        print(f"[INFO] Objectif: {args.target_per_shard} wallets par shard")
    else:
        print(f"[INFO] Objectif: {args.count} wallets au total")
    if args.dry_run:
        print("[INFO] DRY-RUN – aucun fichier ne sera écrit\n")

    while not done():
        secret_key = UserSecretKey.generate()
        pubkey = secret_key.generate_public_key()
        address = pubkey.to_address(hrp="erd")
        shard = address_computer.get_shard_of_address(address)

        generated_total += 1

        # Ignorer ce shard s'il est déjà plein
        if targets and counters[shard] >= targets[shard]:
            continue

        wallet_index = counters[shard]
        counters[shard] += 1

        if args.dry_run:
            print(f"  shard-{shard} [{wallet_index:03d}] {address.to_bech32()}")
            continue

        pem_path = output_dir / f"shard-{shard}" / f"wallet_{wallet_index:03d}.pem"
        write_pem(secret_key, pem_path)

        if sum(counters.values()) % 50 == 0:
            status = "  ".join(f"shard-{s}: {counters[s]}" for s in range(num_shards))
            print(f"[INFO] {status} | généré: {generated_total}")

    print()
    total_written = sum(counters.values())
    print(f"[DONE] {total_written} wallets {'listés' if args.dry_run else 'écrits'} (généré {generated_total} candidats)")
    for s in range(num_shards):
        shard_dir = output_dir / f"shard-{s}"
        print(f"  shard-{s}: {counters[s]} wallets → {shard_dir}")


if __name__ == "__main__":
    main()
