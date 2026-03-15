"""
Affiche les balances EGLD + ESDT de tous les wallets d'un ou plusieurs dossiers.
Optionnellement, top-up automatique des wallets sous un seuil minimum.

Usage:
  # Affichage seul
  python check_balances.py \
    --wallets-dir ./wallets/shard-0 \
    --wallets-dir ./wallets/shard-1 \
    --wallets-dir ./wallets/shard-2 \
    --relayer ./wallets/relayer0.pem \
    --relayer ./wallets/relayer1.pem \
    --relayer ./wallets/relayer2.pem \
    --max-wallets 30

  # Top-up automatique (EGLD + ESDT)
  python check_balances.py \
    --wallets-dir ./wallets/shard-0 \
    --wallets-dir ./wallets/shard-1 \
    --wallets-dir ./wallets/shard-2 \
    --relayer ./wallets/relayer0.pem \
    --relayer ./wallets/relayer1.pem \
    --relayer ./wallets/relayer2.pem \
    --from-wallet ./wallets/bon_supernova.pem \
    --max-wallets 30 \
    --min-egld 0.1 \
    --min-esdt WEGLD-bd4d79 0.05 \
    --dry-run







    python check_balances.py \
    --relayer ./wallets/relayer0.pem \
    --relayer ./wallets/relayer1.pem \
    --relayer ./wallets/relayer2.pem \
    --from-wallet ./wallets/bon_supernova.pem \
    --max-wallets 3 \
    --min-egld 5 \
    --dry-run

   python check_balances.py \
    --wallets-dir ./guild-wallets/shard-0 \
    --wallets-dir ./guild-wallets/shard-1 \
    --wallets-dir ./guild-wallets/shard-2 \
    --from-wallet ./wallets/bon_supernova.pem \
    --max-wallets 450

    



        python check_balances.py \
    --wallets-dir ./windowb-wallets/shard-0 \
    --wallets-dir ./windowb-wallets/shard-1 \
    --wallets-dir ./windowb-wallets/shard-2 \
    --from-wallet ./wallets/bon_supernova.pem \
    --max-wallets 500 \
    --min-egld 0.1 \
    --dry-run

        python check_balances.py \
    --wallets-dir ./windowb-wallets/shard-2 \
    --from-wallet ./wallets/bon_supernova.pem \
    --max-wallets 166 \
    --dry-run
"""
import argparse
import sys
import threading
from pathlib import Path

import requests
from multiversx_sdk import Address, Transaction, TransactionComputer

import config
import utils

EGLD_DECIMALS = 10 ** 18
DEFAULT_TOKENS = ["WEGLD-bd4d79", "USDC-c76f1f"]
TOKEN_DECIMALS: dict[str, int] = {
    "USDC-c76f1f": 6,
}
MAX_THREADS = 20
GAS_EGLD_TOPUP = 50_000
GAS_ESDT_TOPUP = 500_000


def get_egld_balance(address_bech32: str, gateway_url: str) -> int:
    """Retourne la balance EGLD en raw (int)."""
    r = requests.get(f"{gateway_url.rstrip('/')}/address/{address_bech32}", timeout=10)
    r.raise_for_status()
    data = r.json().get("data", {}).get("account", {})
    return int(data.get("balance", 0))


def get_esdt_balances(address_bech32: str, gateway_url: str) -> dict[str, int]:
    """Retourne un dict {tokenId: balance_raw} pour tous les ESDT du wallet."""
    r = requests.get(f"{gateway_url.rstrip('/')}/address/{address_bech32}/esdt", timeout=10)
    r.raise_for_status()
    esdts = r.json().get("data", {}).get("esdts", {})
    return {tid: int(info.get("balance", 0)) for tid, info in esdts.items()}


def fmt(raw: int, decimals: int = 18, precision: int = 4) -> str:
    """Formate une balance raw en unités humaines."""
    if raw == 0:
        return "0"
    val = raw / (10 ** decimals)
    return f"{val:.{precision}f}"


def encode_amount_hex(amount: int) -> str:
    if amount == 0:
        return "00"
    h = hex(amount)[2:]
    return h if len(h) % 2 == 0 else "0" + h


def build_esdt_data(token_id: str, amount_raw: int) -> bytes:
    return f"ESDTTransfer@{token_id.encode().hex()}@{encode_amount_hex(amount_raw)}".encode()


def check_wallet(pem_path: str, tokens: list[str], gateway_url: str) -> dict:
    """Fetch toutes les balances pour un wallet. Retourne un dict de résultats."""
    try:
        signer = utils.load_signer(pem_path)
        address = utils.get_address(signer)
        bech32 = address.to_bech32()

        egld_raw = get_egld_balance(bech32, gateway_url)
        esdt_all = get_esdt_balances(bech32, gateway_url)

        token_balances = {t: esdt_all.get(t, 0) for t in tokens}

        return {
            "name": Path(pem_path).name,
            "address": bech32,
            "egld": egld_raw,
            "tokens": token_balances,
            "error": None,
        }
    except Exception as e:
        return {
            "name": Path(pem_path).name,
            "address": "?",
            "egld": 0,
            "tokens": {t: 0 for t in tokens},
            "error": str(e),
        }


def main():
    parser = argparse.ArgumentParser(description="Vérifier les balances EGLD + ESDT des wallets")
    parser.add_argument("--wallets-dir", action="append", dest="wallets_dirs", default=None,
                        help="Dossier contenant des .pem (répétable, récursif)")
    parser.add_argument("--relayer", action="append", dest="relayers", default=None,
                        help="Fichier PEM individuel à inclure (répétable, ex: ./wallets/relayer0.pem)")
    parser.add_argument("--token", action="append", dest="tokens", default=None,
                        help=f"Token ESDT à afficher (répétable, défaut: {DEFAULT_TOKENS})")
    parser.add_argument("--max-wallets", type=int, default=0,
                        help="Limiter le nombre de wallets par dossier (0 = tous)")
    # Top-up
    parser.add_argument("--from-wallet", default=None,
                        help="Wallet principal pour les top-ups (requis si --min-egld ou --min-esdt)")
    parser.add_argument("--min-egld", type=float, default=None,
                        help="Seuil EGLD minimum : envoie le déficit aux wallets en dessous")
    parser.add_argument("--min-esdt", nargs=2, action="append", metavar=("TOKEN_ID", "AMOUNT"),
                        default=None,
                        help="Seuil ESDT minimum (répétable) : --min-esdt WEGLD-bd4d79 0.05")
    parser.add_argument("--dry-run", action="store_true",
                        help="Construire/afficher les top-ups sans envoyer")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    if not args.wallets_dirs and not args.relayers:
        parser.error("Fournir au moins --wallets-dir ou --relayer")

    topup_enabled = args.min_egld is not None or args.min_esdt is not None
    if topup_enabled and not args.from_wallet:
        parser.error("--from-wallet est requis avec --min-egld ou --min-esdt")

    tokens = args.tokens if args.tokens else DEFAULT_TOKENS

    # Collecter tous les .pem (récursif dans chaque dossier)
    all_pems = []
    for d in (args.wallets_dirs or []):
        pems = sorted(Path(d).expanduser().rglob("*.pem"))
        if args.max_wallets:
            pems = pems[:args.max_wallets]
        all_pems.extend([str(p) for p in pems])

    # Ajouter les PEM individuels (relayers, bon_supernova, etc.)
    for pem in (args.relayers or []):
        p = Path(pem).expanduser()
        if not p.exists():
            print(f"[WARN] Fichier introuvable : {pem}", file=sys.stderr)
        elif str(p) not in all_pems:
            all_pems.append(str(p))

    if not all_pems:
        print("[ERROR] Aucun .pem trouvé", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] {len(all_pems)} wallets | tokens: {', '.join(tokens)} | gateway: {args.gateway}\n")

    # Fetch en parallèle
    results = [None] * len(all_pems)
    semaphore = threading.Semaphore(MAX_THREADS)

    def fetch(i, pem):
        with semaphore:
            results[i] = check_wallet(pem, tokens, args.gateway)

    threads = [threading.Thread(target=fetch, args=(i, p)) for i, p in enumerate(all_pems)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Affichage
    col_addr = 20
    col_egld = 14
    col_tok = 14

    # Header
    header = f"{'Wallet':<20} {'Address':<20} {'EGLD':>{col_egld}}"
    for tok in tokens:
        label = tok.split("-")[0]  # ex: WEGLD
        header += f"  {label:>{col_tok}}"
    print(header)
    print("-" * len(header))

    totals_egld = 0
    totals_tokens = {t: 0 for t in tokens}

    for r in results:
        if r["error"]:
            print(f"{'!' + r['name']:<20} {'ERROR':<20} {r['error']}")
            continue

        addr_short = r["address"][:8] + "…" + r["address"][-6:]
        line = f"{r['name']:<20} {addr_short:<20} {fmt(r['egld']):>{col_egld}}"
        for tok in tokens:
            dec = TOKEN_DECIMALS.get(tok, 18)
            line += f"  {fmt(r['tokens'][tok], decimals=dec):>{col_tok}}"
        print(line)

        totals_egld += r["egld"]
        for tok in tokens:
            totals_tokens[tok] += r["tokens"][tok]

    # Totaux
    print("-" * len(header))
    total_line = f"{'TOTAL':<20} {'':<20} {fmt(totals_egld):>{col_egld}}"
    for tok in tokens:
        dec = TOKEN_DECIMALS.get(tok, 18)
        total_line += f"  {fmt(totals_tokens[tok], decimals=dec):>{col_tok}}"
    print(total_line)

    # --- Top-up ---
    if not topup_enabled:
        return

    print(f"\n{'=' * len(header)}")
    if args.dry_run:
        print("[DRY-RUN] Top-up — aucune transaction ne sera envoyée\n")
    else:
        print("[INFO] Top-up en cours...\n")

    from_signer = utils.load_signer(args.from_wallet)
    from_address = utils.get_address(from_signer)
    from_bech32 = from_address.to_bech32()
    provider = utils.get_provider(args.gateway)
    computer = TransactionComputer()

    nonce = utils.get_account_nonce(provider, from_address)
    topup_count = 0

    min_egld_raw = int(args.min_egld * EGLD_DECIMALS) if args.min_egld is not None else None
    min_esdt_list = []  # [(token_id, min_raw)]
    for token_id, amount_str in (args.min_esdt or []):
        dec = TOKEN_DECIMALS.get(token_id, 18)
        min_esdt_list.append((token_id, int(float(amount_str) * (10 ** dec))))

    for r in results:
        if r["error"] or r["address"] == from_bech32:
            continue

        receiver = Address.new_from_bech32(r["address"])

        # EGLD top-up
        if min_egld_raw is not None and r["egld"] < min_egld_raw:
            deficit = min_egld_raw - r["egld"]
            tag = "[DRY-RUN]" if args.dry_run else "[TOPUP]"
            print(f"{tag} EGLD → {r['name']} ({r['address'][:12]}…) "
                  f"+{fmt(deficit)} EGLD (balance: {fmt(r['egld'])})")
            tx = Transaction(
                nonce=nonce,
                sender=from_address,
                receiver=receiver,
                value=deficit,
                gas_limit=GAS_EGLD_TOPUP,
                data=b"",
                chain_id=config.CHAIN_ID,
                gas_price=config.DEFAULT_GAS_PRICE,
                version=config.DEFAULT_TX_VERSION,
            )
            tx.signature = from_signer.sign(computer.compute_bytes_for_signing(tx))
            nonce += 1
            topup_count += 1
            if not args.dry_run:
                try:
                    provider.send_transaction(tx)
                except Exception as e:
                    print(f"[WARN] EGLD top-up failed for {r['name']}: {e}")

        # ESDT top-up
        for token_id, min_raw in min_esdt_list:
            current = r["tokens"].get(token_id, 0)
            if current < min_raw:
                deficit = min_raw - current
                dec = TOKEN_DECIMALS.get(token_id, 18)
                tag = "[DRY-RUN]" if args.dry_run else "[TOPUP]"
                print(f"{tag} {token_id.split('-')[0]} → {r['name']} ({r['address'][:12]}…) "
                      f"+{fmt(deficit, decimals=dec)} (balance: {fmt(current, decimals=dec)})")
                tx = Transaction(
                    nonce=nonce,
                    sender=from_address,
                    receiver=receiver,
                    value=0,
                    gas_limit=GAS_ESDT_TOPUP,
                    data=build_esdt_data(token_id, deficit),
                    chain_id=config.CHAIN_ID,
                    gas_price=config.DEFAULT_GAS_PRICE,
                    version=config.DEFAULT_TX_VERSION,
                )
                tx.signature = from_signer.sign(computer.compute_bytes_for_signing(tx))
                nonce += 1
                topup_count += 1
                if not args.dry_run:
                    try:
                        provider.send_transaction(tx)
                    except Exception as e:
                        print(f"[WARN] ESDT top-up failed for {r['name']}: {e}")

    tag = "[DRY-RUN]" if args.dry_run else "[DONE]"
    print(f"\n{tag} {topup_count} top-up(s) {'préparés' if args.dry_run else 'envoyés'}")


if __name__ == "__main__":
    main()
