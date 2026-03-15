"""
Génère des wallets MultiversX et les trie par shard dans des sous-dossiers.

Structure de sortie :
  <output-dir>/
    shard-0/  wallet_000.pem  wallet_001.pem  …
    shard-1/  wallet_000.pem  …
    shard-2/  wallet_000.pem  …

Usage :
  # S'arrêter quand chaque shard a 100 wallets (recommandé pour le challenge)
  python generate_wallets.py --target-per-shard 100 --output-dir ./wallets

  # Générer exactement 300 wallets, triés par shard
  python generate_wallets.py --count 300 --output-dir ./wallets

  # Aperçu sans écrire de fichiers
  python generate_wallets.py --count 10 --dry-run

  python generate_wallets.py \
  --num-shards 3  --output-dir ./windowb-wallets \
  --target-per-shard 167 \
  --dry-run



"""
import argparse
import base64
import sys
from pathlib import Path

from multiversx_sdk import AddressComputer, UserSecretKey


def write_pem(secret_key: UserSecretKey, output_path: Path) -> None:
    """
    Écrit un fichier PEM MultiversX à partir d'une UserSecretKey.

    Format :
      -----BEGIN PRIVATE KEY for {bech32}-----
      {base64 of hex(secret_32_bytes + pubkey_32_bytes), wrapped at 64 chars}
      -----END PRIVATE KEY for {bech32}-----
    """
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
        help="Nombre total de wallets à générer",
    )
    group.add_argument(
        "--target-per-shard", type=int, default=None,
        help="S'arrête quand chaque shard a ce nombre de wallets (ex: 100)",
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

    if args.count is None and args.target_per_shard is None:
        parser.error("Spécifiez --count ou --target-per-shard")

    num_shards = args.num_shards
    address_computer = AddressComputer(number_of_shards=num_shards)

    # Compteurs et index par shard
    counters: dict[int, int] = {s: 0 for s in range(num_shards)}
    output_dir = Path(args.output_dir).expanduser()

    # Créer les sous-dossiers
    if not args.dry_run:
        for s in range(num_shards):
            (output_dir / f"shard-{s}").mkdir(parents=True, exist_ok=True)

    generated_total = 0

    def done() -> bool:
        if args.target_per_shard is not None:
            return all(counters[s] >= args.target_per_shard for s in range(num_shards))
        return generated_total >= args.count  # type: ignore[operator]

    print(f"[INFO] Réseau: {num_shards} shards | Sortie: {output_dir}")
    if args.target_per_shard:
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

        # En mode --target-per-shard, ignorer ce shard s'il est déjà plein
        if args.target_per_shard is not None and counters[shard] >= args.target_per_shard:
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

    # Résumé final
    print()
    total_written = sum(counters.values())
    print(f"[DONE] {total_written} wallets {'listés' if args.dry_run else 'écrits'} (généré {generated_total} candidats)")
    for s in range(num_shards):
        shard_dir = output_dir / f"shard-{s}"
        print(f"  shard-{s}: {counters[s]} wallets → {shard_dir}")


if __name__ == "__main__":
    main()
