"""
Affiche les balances EGLD + ESDT de tous les wallets d'un ou plusieurs dossiers.
Optionnellement, top-up automatique des wallets sous un seuil minimum.

v3 — correctifs vs v2 :
  - Retry HTTP (3 tentatives, 2s backoff) dans get_egld_balance / get_esdt_balances
  - MAX_THREADS réduit à 10 (moins de pression sur le gateway)
  - Retry send_transaction (3 tentatives, 1s backoff + re-fetch nonce + re-signature)
  - Skip ESDT fetch si pas de --min-esdt (réduit les appels API)

Usage:
  # Affichage seul
  python check_balances_3.py \
    --wallets-dir ./spam-wallets/shard-0 \
    --wallets-dir ./spam-wallets/shard-1 \
    --wallets-dir ./spam-wallets/shard-2 \
    --max-wallets 500 \
    --gateway http://192.168.1.23:8079

  # Top-up EGLD
  python check_balances_3.py \
    --wallets-dir ./spam-wallets/shard-0 \
    --wallets-dir ./spam-wallets/shard-1 \
    --wallets-dir ./spam-wallets/shard-2 \
    --from-wallet ./wallets/bon_supernova.pem \
    --max-wallets 500 \
    --gateway http://192.168.1.23:8079 \
    --min-egld 2

  # Dry-run
  python check_balances_3.py \
    --wallets-dir ./spam-wallets/shard-0 \
    --wallets-dir ./spam-wallets/shard-1 \
    --wallets-dir ./spam-wallets/shard-2 \
    --from-wallet ./wallets/bon_supernova.pem \
    --max-wallets 500 \
    --gateway http://192.168.1.23:8079 \
    --min-egld 2 \
    --dry-run
"""
import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from multiversx_sdk import Address, AddressComputer, Transaction, TransactionComputer

import config
import utils

EGLD_DECIMALS = 10 ** 18
DEFAULT_TOKENS = ["WEGLD-bd4d79", "USDC-c76f1f"]
TOKEN_DECIMALS: dict[str, int] = {
    "USDC-c76f1f": 6,
}
MAX_THREADS    = 10   # réduit de 20 à 10 pour ménager le gateway
GAS_EGLD_TOPUP = 50_000
GAS_ESDT_TOPUP = 500_000
GAS_WRAP       = 3_000_000

WEGLD_WRAP_SC = {
    0: "erd1qqqqqqqqqqqqqpgqvc7gdl0p4s97guh498wgz75k8sav6sjfjlwqh679jy",
    1: "erd1qqqqqqqqqqqqqpgqhe8t5jewej70zupmh44jurgn29psua5l2jps3ntjj3",
    2: "erd1qqqqqqqqqqqqqpgqmuk0q2saj0mgutxm4teywre6dl8wqf58xamqdrukln",
}

HTTP_TIMEOUT    = 10    # secondes
HTTP_RETRIES    = 3
HTTP_BACKOFF    = 2.0   # secondes entre tentatives
SEND_RETRIES    = 3
SEND_BACKOFF    = 1.0


# ---------------------------------------------------------------------------
# HTTP helpers avec retry
# ---------------------------------------------------------------------------

def http_get_with_retry(url: str) -> dict:
    """GET JSON avec retry automatique sur timeout/erreur réseau."""
    last_err = None
    for attempt in range(HTTP_RETRIES):
        try:
            r = requests.get(url, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < HTTP_RETRIES - 1:
                time.sleep(HTTP_BACKOFF)
    raise last_err


def get_egld_balance(address_bech32: str, gateway_url: str) -> tuple[int, int]:
    """Retourne (balance EGLD en raw, nonce)."""
    data = http_get_with_retry(
        f"{gateway_url.rstrip('/')}/address/{address_bech32}"
    ).get("data", {}).get("account", {})
    return int(data.get("balance", 0)), int(data.get("nonce", 0))


def get_esdt_balances(address_bech32: str, gateway_url: str) -> dict[str, int]:
    """Retourne un dict {tokenId: balance_raw} pour tous les ESDT du wallet."""
    esdts = http_get_with_retry(
        f"{gateway_url.rstrip('/')}/address/{address_bech32}/esdt"
    ).get("data", {}).get("esdts", {})
    return {tid: int(info.get("balance", 0)) for tid, info in esdts.items()}


# ---------------------------------------------------------------------------
# Formatage
# ---------------------------------------------------------------------------

def fmt(raw: int, decimals: int = 18, precision: int = 4) -> str:
    if raw == 0:
        return "0"
    return f"{raw / (10 ** decimals):.{precision}f}"


def encode_amount_hex(amount: int) -> str:
    if amount == 0:
        return "00"
    h = hex(amount)[2:]
    return h if len(h) % 2 == 0 else "0" + h


def build_esdt_data(token_id: str, amount_raw: int) -> bytes:
    return f"ESDTTransfer@{token_id.encode().hex()}@{encode_amount_hex(amount_raw)}".encode()


# ---------------------------------------------------------------------------
# Shard helpers
# ---------------------------------------------------------------------------

_address_computer = AddressComputer(number_of_shards=3)


def get_shard(address: Address) -> int:
    return _address_computer.get_shard_of_address(address)


def expected_shard_from_path(pem_path: str) -> int | None:
    import re
    for part in Path(pem_path).parts:
        m = re.match(r"shard-(\d+)", part)
        if m:
            return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Check wallet (lecture parallèle)
# ---------------------------------------------------------------------------

def check_wallet(pem_path: str, tokens: list[str], gateway_url: str,
                 fetch_esdt: bool) -> dict:
    """Fetch toutes les balances pour un wallet. Retourne un dict de résultats."""
    try:
        signer  = utils.load_signer(pem_path)
        address = utils.get_address(signer)
        bech32  = address.to_bech32()
        shard   = get_shard(address)

        expected = expected_shard_from_path(pem_path)
        wrong_dir = expected is not None and shard != expected

        egld_raw, nonce = get_egld_balance(bech32, gateway_url)
        esdt_all = get_esdt_balances(bech32, gateway_url) if fetch_esdt else {}

        token_balances = {t: esdt_all.get(t, 0) for t in tokens}

        prefix = f"[!shard{expected}→{shard}]" if wrong_dir else f"{shard}_"
        return {
            "name":      f"{prefix}{Path(pem_path).name}",
            "shard":     shard,
            "wrong_dir": wrong_dir,
            "address":   bech32,
            "egld":      egld_raw,
            "nonce":     nonce,
            "tokens":    token_balances,
            "pem_path":  pem_path,
            "error":     None,
        }
    except Exception as e:
        return {
            "name":      Path(pem_path).name,
            "address":   "?",
            "egld":      0,
            "nonce":     -1,
            "tokens":    {t: 0 for t in tokens},
            "wrong_dir": False,
            "error":     str(e),
        }


# ---------------------------------------------------------------------------
# Build/sign helpers (pour le retry avec re-nonce)
# ---------------------------------------------------------------------------

def build_and_sign_egld_tx(signer, computer, nonce: int, sender, receiver,
                            value: int) -> Transaction:
    tx = Transaction(
        nonce=nonce,
        sender=sender,
        receiver=receiver,
        value=value,
        gas_limit=GAS_EGLD_TOPUP,
        data=b"",
        chain_id=config.CHAIN_ID,
        gas_price=config.DEFAULT_GAS_PRICE,
        version=config.DEFAULT_TX_VERSION,
    )
    tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
    return tx


def build_and_sign_wrap_tx(signer, computer, nonce: int, sender,
                            wrap_sc: Address, amount: int) -> Transaction:
    tx = Transaction(
        nonce=nonce,
        sender=sender,
        receiver=wrap_sc,
        value=amount,
        gas_limit=GAS_WRAP,
        data=b"wrapEgld",
        chain_id=config.CHAIN_ID,
        gas_price=config.DEFAULT_GAS_PRICE,
        version=config.DEFAULT_TX_VERSION,
    )
    tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
    return tx


def build_and_sign_esdt_tx(signer, computer, nonce: int, sender, receiver,
                            token_id: str, amount: int) -> Transaction:
    tx = Transaction(
        nonce=nonce,
        sender=sender,
        receiver=receiver,
        value=0,
        gas_limit=GAS_ESDT_TOPUP,
        data=build_esdt_data(token_id, amount),
        chain_id=config.CHAIN_ID,
        gas_price=config.DEFAULT_GAS_PRICE,
        version=config.DEFAULT_TX_VERSION,
    )
    tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
    return tx


def send_with_retry(tx: Transaction, provider, signer, computer, nonce_ref: list,
                    from_address, rebuild_fn, rebuild_kwargs: dict,
                    label: str) -> bool:
    """
    Tente d'envoyer tx jusqu'à SEND_RETRIES fois.
    Sur échec : sleep, re-fetch nonce, re-build + re-sign, retry.
    nonce_ref est une liste [nonce] pour pouvoir le modifier par référence.
    Retourne True si succès, False si tous les essais ont échoué.
    """
    for attempt in range(SEND_RETRIES):
        try:
            provider.send_transaction(tx)
            nonce_ref[0] += 1
            return True
        except Exception as e:
            if attempt < SEND_RETRIES - 1:
                print(f"[RETRY] {label} attempt {attempt + 1}/{SEND_RETRIES}: {e}")
                time.sleep(SEND_BACKOFF)
                try:
                    nonce_ref[0] = utils.get_account_nonce(provider, from_address)
                    tx = rebuild_fn(nonce=nonce_ref[0], **rebuild_kwargs)
                except Exception as re_err:
                    print(f"[WARN] nonce re-fetch failed: {re_err}")
            else:
                print(f"[WARN] {label} failed after {SEND_RETRIES} attempts: {e}")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Vérifier les balances EGLD + ESDT des wallets (v3)"
    )
    parser.add_argument("--wallets-dir", action="append", dest="wallets_dirs", default=None,
                        help="Dossier contenant des .pem (répétable)")
    parser.add_argument("--relayer", action="append", dest="relayers", default=None,
                        help="Fichier PEM individuel à inclure (répétable)")
    parser.add_argument("--token", action="append", dest="tokens", default=None,
                        help=f"Token ESDT à afficher (répétable, défaut: {DEFAULT_TOKENS})")
    parser.add_argument("--max-wallets", type=int, default=0,
                        help="Limiter le nombre de wallets par dossier (0 = tous)")
    parser.add_argument("--from-wallet", default=None,
                        help="Wallet principal pour les top-ups")
    parser.add_argument("--min-egld", type=float, default=None,
                        help="Seuil EGLD minimum : envoie le déficit aux wallets en dessous")
    parser.add_argument("--min-esdt", nargs=2, action="append",
                        metavar=("TOKEN_ID", "AMOUNT"), default=None,
                        help="Seuil ESDT minimum (répétable)")
    parser.add_argument("--wrap-wegld", type=float, default=None, metavar="AMOUNT",
                        help="Wrap l'EGLD manquant depuis chaque wallet sous le seuil WEGLD "
                             "(ex: 0.1). Chaque wallet wrape lui-même, sans --from-wallet.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Construire/afficher les top-ups sans envoyer")
    parser.add_argument("--save", default=None, metavar="FILE",
                        help="Enregistrer les balances dans un fichier JSON")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    if not args.wallets_dirs and not args.relayers:
        parser.error("Fournir au moins --wallets-dir ou --relayer")

    wrap_wegld_mode = args.wrap_wegld is not None
    topup_enabled   = args.min_egld is not None or args.min_esdt is not None or wrap_wegld_mode
    if topup_enabled and not wrap_wegld_mode and not args.from_wallet:
        parser.error("--from-wallet est requis avec --min-egld ou --min-esdt")

    tokens      = args.tokens if args.tokens else DEFAULT_TOKENS
    fetch_esdt  = bool(args.min_esdt or args.tokens or wrap_wegld_mode)  # skip si pas besoin

    # Collecter tous les .pem — max_wallets distribué équitablement entre les dossiers.
    # Ex : max_wallets=100, 3 dossiers → 34 + 33 + 33
    all_pems = []
    dirs = args.wallets_dirs or []
    n_dirs = len(dirs)
    for i, d in enumerate(dirs):
        pems = sorted(Path(d).expanduser().rglob("*.pem"))
        if args.max_wallets and n_dirs > 0:
            base, extra = divmod(args.max_wallets, n_dirs)
            pems = pems[:base + (1 if i < extra else 0)]
        all_pems.extend([str(p) for p in pems])

    for pem in (args.relayers or []):
        p = Path(pem).expanduser()
        if not p.exists():
            print(f"[WARN] Fichier introuvable : {pem}", file=sys.stderr)
        elif str(p) not in all_pems:
            all_pems.append(str(p))

    if not all_pems:
        print("[ERROR] Aucun .pem trouvé", file=sys.stderr)
        sys.exit(1)

    esdt_note = "" if fetch_esdt else " (ESDT skipped)"
    print(f"[INFO] {len(all_pems)} wallets | tokens: {', '.join(tokens)} "
          f"| threads: {MAX_THREADS}{esdt_note} | gateway: {args.gateway}\n")

    # Fetch en parallèle
    results   = [None] * len(all_pems)
    semaphore = threading.Semaphore(MAX_THREADS)

    def fetch(i, pem):
        with semaphore:
            results[i] = check_wallet(pem, tokens, args.gateway, fetch_esdt)

    threads = [threading.Thread(target=fetch, args=(i, p)) for i, p in enumerate(all_pems)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    results.sort(key=lambda r: (r["shard"] if r["error"] is None else 99, r["name"]))

    # Affichage
    col_addr  = 62
    col_egld  = 14
    col_tok   = 14
    col_nonce = 8

    header = f"{'Wallet':<20} {'Address':<{col_addr}} {'EGLD':>{col_egld}} {'Nonce':>{col_nonce}}"
    for tok in tokens:
        header += f"  {tok.split('-')[0]:>{col_tok}}"
    print(header)
    print("-" * len(header))

    totals_egld   = 0
    totals_nonce  = 0
    totals_tokens = {t: 0 for t in tokens}
    error_count   = 0

    for r in results:
        if r["error"]:
            print(f"{'!' + r['name']:<20} {'ERROR':<20} {r['error']}")
            error_count += 1
            continue

        line = (f"{r['name']:<20} {r['address']:<{col_addr}} "
                f"{fmt(r['egld']):>{col_egld}} {r['nonce']:>{col_nonce}}")
        for tok in tokens:
            dec = TOKEN_DECIMALS.get(tok, 18)
            line += f"  {fmt(r['tokens'][tok], decimals=dec):>{col_tok}}"
        print(line)

        totals_egld  += r["egld"]
        totals_nonce += r["nonce"]
        for tok in tokens:
            totals_tokens[tok] += r["tokens"][tok]

    print("-" * len(header))
    total_line = (f"{'TOTAL':<20} {'':<{col_addr}} "
                  f"{fmt(totals_egld):>{col_egld}} {totals_nonce:>{col_nonce}}")
    for tok in tokens:
        dec = TOKEN_DECIMALS.get(tok, 18)
        total_line += f"  {fmt(totals_tokens[tok], decimals=dec):>{col_tok}}"
    print(total_line)

    if error_count:
        print(f"\n[WARN] {error_count} wallet(s) en erreur (lecture gateway échouée)")

    # Sauvegarde JSON
    if args.save:
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "gateway":   args.gateway,
            "wallets": [
                {
                    "name":       r["name"],
                    "address":    r["address"],
                    "shard":      r.get("shard"),
                    "egld":       fmt(r["egld"]),
                    "egld_raw":   r["egld"],
                    "nonce":      r["nonce"],
                    "tokens":     {t: fmt(v, decimals=TOKEN_DECIMALS.get(t, 18))
                                   for t, v in r["tokens"].items()},
                    "tokens_raw": r["tokens"],
                    "error":      r["error"],
                }
                for r in results
            ],
        }
        Path(args.save).write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
        print(f"\n[SAVE] {len(results)} wallets → {args.save}")

    # Avertissement wallets mal classés
    wrong = [r for r in results if r.get("wrong_dir")]
    if wrong:
        print(f"\n[WARN] {len(wrong)} wallet(s) dans le mauvais dossier shard :")
        for r in wrong:
            print(f"  {r['name']}  →  real shard {r['shard']}  |  {r['address']}")

    # --- Top-up ---
    if not topup_enabled:
        return

    print(f"\n{'=' * len(header)}")
    if args.dry_run:
        print("[DRY-RUN] Top-up — aucune transaction ne sera envoyée\n")
    else:
        print("[INFO] Top-up en cours...\n")

    provider  = utils.get_provider(args.gateway)
    computer  = TransactionComputer()

    topup_count  = 0
    topup_failed = 0

    # --- Mode wrap-wegld : chaque wallet wrape lui-même ---
    if wrap_wegld_mode:
        min_raw = int(args.wrap_wegld * EGLD_DECIMALS)
        for r in results:
            if r["error"]:
                continue
            current = r["tokens"].get("WEGLD-bd4d79", 0)
            if current >= min_raw:
                continue
            deficit = min_raw - current
            gas_cost = GAS_WRAP * config.DEFAULT_GAS_PRICE
            if r["egld"] < deficit + gas_cost:
                print(f"[SKIP] {r['name']} : EGLD insuffisant pour wrap "
                      f"({fmt(r['egld'])} < {fmt(deficit + gas_cost)})")
                continue
            shard = r.get("shard", 0)
            wrap_sc = Address.new_from_bech32(WEGLD_WRAP_SC[shard])
            tag = "[DRY-RUN]" if args.dry_run else "[WRAP]"
            dec = TOKEN_DECIMALS.get("WEGLD-bd4d79", 18)
            print(f"{tag} wrapEgld {r['name']} ({r['address']}) "
                  f"+{fmt(deficit, decimals=dec)} WEGLD (balance: {fmt(current, decimals=dec)})")
            if not args.dry_run:
                wallet_signer  = utils.load_signer(r["pem_path"])
                wallet_address = utils.get_address(wallet_signer)
                nonce_ref      = [r["nonce"]]
                tx = build_and_sign_wrap_tx(
                    wallet_signer, computer, nonce_ref[0], wallet_address, wrap_sc, deficit
                )
                ok = send_with_retry(
                    tx, provider, wallet_signer, computer, nonce_ref, wallet_address,
                    rebuild_fn=lambda nonce, **kw: build_and_sign_wrap_tx(
                        wallet_signer, computer, nonce, wallet_address, wrap_sc, deficit
                    ),
                    rebuild_kwargs={},
                    label=f"wrap→{r['name']}",
                )
                if not ok:
                    topup_failed += 1
            topup_count += 1
    else:
        from_signer  = utils.load_signer(args.from_wallet)
        from_address = utils.get_address(from_signer)
        from_bech32  = from_address.to_bech32()
        nonce_ref    = [utils.get_account_nonce(provider, from_address)]

        min_egld_raw  = int(args.min_egld * EGLD_DECIMALS) if args.min_egld is not None else None
        min_esdt_list = []
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
                print(f"{tag} EGLD → {r['name']} ({r['address']}) "
                      f"+{fmt(deficit)} EGLD (balance: {fmt(r['egld'])})")
                tx = build_and_sign_egld_tx(
                    from_signer, computer, nonce_ref[0], from_address, receiver, deficit
                )
                topup_count += 1
                if args.dry_run:
                    nonce_ref[0] += 1
                else:
                    ok = send_with_retry(
                        tx, provider, from_signer, computer, nonce_ref, from_address,
                        rebuild_fn=lambda nonce, **kw: build_and_sign_egld_tx(
                            from_signer, computer, nonce, from_address, receiver, deficit
                        ),
                        rebuild_kwargs={},
                        label=f"EGLD→{r['name']}",
                    )
                    if not ok:
                        topup_failed += 1

            # ESDT top-up
            for token_id, min_raw in min_esdt_list:
                current = r["tokens"].get(token_id, 0)
                if current < min_raw:
                    deficit = min_raw - current
                    dec = TOKEN_DECIMALS.get(token_id, 18)
                    tag = "[DRY-RUN]" if args.dry_run else "[TOPUP]"
                    print(f"{tag} {token_id.split('-')[0]} → {r['name']} ({r['address']}) "
                          f"+{fmt(deficit, decimals=dec)} (balance: {fmt(current, decimals=dec)})")
                    tx = build_and_sign_esdt_tx(
                        from_signer, computer, nonce_ref[0], from_address, receiver,
                        token_id, deficit
                    )
                    topup_count += 1
                    if args.dry_run:
                        nonce_ref[0] += 1
                    else:
                        ok = send_with_retry(
                            tx, provider, from_signer, computer, nonce_ref, from_address,
                            rebuild_fn=lambda nonce, **kw: build_and_sign_esdt_tx(
                                from_signer, computer, nonce, from_address, receiver,
                                token_id, deficit
                            ),
                            rebuild_kwargs={},
                            label=f"{token_id.split('-')[0]}→{r['name']}",
                        )
                        if not ok:
                            topup_failed += 1

    tag     = "[DRY-RUN]" if args.dry_run else "[DONE]"
    summary = f"{topup_count} top-up(s) {'préparés' if args.dry_run else 'envoyés'}"
    if topup_failed:
        summary += f"  ({topup_failed} échoués après {SEND_RETRIES} tentatives)"
    print(f"\n{tag} {summary}")


if __name__ == "__main__":
    main()
