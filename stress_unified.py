"""
stress_unified.py — Script de stress test unifié pour les 5 fenêtres BoN.

Modes (--mode) :
  intra    : MoveBalance intra-shard                        [Window A]
  cross    : MoveBalance cross-shard                        [Window C]
  mixed    : MoveBalance mix intra+cross (--cross-ratio)    [bonus]
  dex      : ESDTTransfer → DEX SC                          [Window B]

Ajouter --relayer-pem pour activer RelayedV3 sur n'importe quel mode :
  cross + relayed → Relayed MoveBalance cross-shard         [Window D]
  dex   + relayed → Relayed DEX SC calls                    [Window E]

Ajouter --target N pour arrêter automatiquement après N txs acceptées.

Exemples :
  # Window A – 50 000 intra-shard
  python stress_unified.py \\
    --wallets-dir ./spam-wallets/shard-0 \\
    --wallets-dir ./spam-wallets/shard-1 \\
    --wallets-dir ./spam-wallets/shard-2 \\
    --mode intra --target 50000

  # Window B – 1 000 DEX SC calls
  python stress_unified.py \\
    --wallets-dir ./spam-wallets/shard-0 \\
    --mode dex \\
    --token-in WEGLD-bd4d79 --token-out MEX-455c57 --amount-in 1000000000000000 \\
    --target 1000

  # Window C – 50 000 cross-shard
  python stress_unified.py \\
    --wallets-dir ./spam-wallets/shard-0 \\
    --wallets-dir ./spam-wallets/shard-1 \\
    --wallets-dir ./spam-wallets/shard-2 \\
    --mode cross --target 50000

  # Window D – 10 000 relayed cross-shard
  python stress_unified.py \\
    --wallets-dir ./spam-wallets/shard-0 \\
    --wallets-dir ./spam-wallets/shard-1 \\
    --wallets-dir ./spam-wallets/shard-2 \\
    --mode cross --relayer-pem ./wallets/relayer.pem --target 10000

  # Window E – 500 relayed DEX SC calls
  python stress_unified.py \\
    --wallets-dir ./spam-wallets/shard-0 \\
    --mode dex --relayer-pem ./wallets/relayer.pem \\
    --token-in WEGLD-bd4d79 --token-out MEX-455c57 --amount-in 1000000000000000 \\
    --target 500

  # Mixed 50/50
  python stress_unified.py \\
    --wallets-dir ./spam-wallets/shard-0 \\
    --wallets-dir ./spam-wallets/shard-1 \\
    --wallets-dir ./spam-wallets/shard-2 \\
    --mode mixed --cross-ratio 0.5 --target 100000
"""
import argparse
import random
import sys
import threading
import time
from pathlib import Path

import requests
from multiversx_sdk import Address, AddressComputer, Transaction, TransactionComputer

import config
import utils

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

GAS_MOVE_BALANCE     = 50_000
GAS_RELAYER_OVERHEAD = 50_000
GAS_DEX_SWAP         = 15_000_000
GAS_WRAP             = 3_000_000

DEFAULT_BATCH_SIZE       = 90
MAX_NONCE_LOOKAHEAD      = 95
MAX_CONCURRENT_API_CALLS = 30

# Rich logic (modes intra / cross / mixed sans relayed)
RICH_THRESHOLD   = int(1 * 1e18)     # en dessous : value = GAS_COST_RAW
DRAIN_RATIO      = 0.20              # au dessus : drain cette fraction par batch
MAX_VALUE_PER_TX = int(5.0 * 1e18)  # plafond par tx


NUM_SHARDS = 3
_address_computer = AddressComputer(number_of_shards=NUM_SHARDS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compute_shard(address) -> int:
    return _address_computer.get_shard_of_address(address)


def get_account_safe(gateway_url: str, address, api_semaphore) -> tuple[int, int]:
    """Retourne (nonce_confirmé, balance_raw) via HTTP direct."""
    url = f"{gateway_url.rstrip('/')}/address/{address.to_bech32()}"
    with api_semaphore:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", {}).get("account", {})
        return int(data.get("nonce", 0)), int(data.get("balance", 0))


def encode_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        return value.encode().hex()
    return value.hex()


def encode_amount_hex(amount: int) -> str:
    if amount == 0:
        return "00"
    h = hex(amount)[2:]
    return h if len(h) % 2 == 0 else "0" + h


def build_swap_data(token_in: str, amount_in_raw: int, token_out: str, min_out: int = 1) -> bytes:
    """Construit la data ESDTTransfer + swapTokensFixedInput pour le DEX."""
    data = "@".join([
        "ESDTTransfer",
        encode_hex(token_in),
        encode_amount_hex(amount_in_raw),
        encode_hex("swapTokensFixedInput"),
        encode_hex(token_out),
        encode_amount_hex(min_out),
    ])
    return data.encode()


def load_wallets(wallets_dirs: list[str], max_wallets: int) -> list[dict]:
    # Distribue max_wallets équitablement entre les dossiers (shards).
    # Ex : max_wallets=100, 3 shards → 34 + 33 + 33
    n = len(wallets_dirs)
    base, extra = divmod(max_wallets, n)
    all_pems = []
    for i, d in enumerate(wallets_dirs):
        limit = base + (1 if i < extra else 0)
        pems = sorted(Path(d).expanduser().glob("*.pem"))[:limit]
        all_pems.extend(pems)
    if not all_pems:
        print("[ERROR] Aucun .pem trouvé", file=sys.stderr)
        sys.exit(1)
    wallets = []
    for p in all_pems:
        signer  = utils.load_signer(str(p))
        address = utils.get_address(signer)
        shard   = compute_shard(address)
        wallets.append({
            "signer":  signer,
            "address": address,
            "name":    p.name,
            "shard":   shard,
        })
    return wallets


def build_routing(wallets: list[dict]) -> list[dict]:
    """Assigne intra_pool et cross_pool à chaque wallet."""
    by_shard: dict[int, list[dict]] = {}
    for w in wallets:
        by_shard.setdefault(w["shard"], []).append(w)

    for shard_id, group in by_shard.items():
        cross_pool = []
        for other_shard, other_group in by_shard.items():
            if other_shard != shard_id:
                cross_pool.extend(other_group)
        for w in group:
            w["intra_pool"] = [x["address"] for x in group if x is not w]
            w["cross_pool"] = [x["address"] for x in cross_pool] if cross_pool else w["intra_pool"]

    return wallets


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def wallet_worker(
    wallet: dict,
    mode: str,              # "intra" | "cross" | "mixed" | "dex"
    is_relayed: bool,
    relayer_addr,           # Address | None
    relayer_signer,         # UserSigner | None
    batch_size: int,
    cross_ratio: float,     # utilisé uniquement en mode mixed
    gas_limit_per_tx: int,
    value: int,             # montant raw fixe par tx (0 = rich logic)
    dex_data: bytes | None,
    amount_in: int,         # EGLD raw à wrapper (mode wrap) ou 0
    min_value: int,         # plancher pour effective_value en rich logic (0 = désactivé)
    gateway_url: str,
    provider,
    dry_run: bool,
    counter: list,          # [total, intra, cross/dex]
    lock: threading.Lock,
    api_semaphore: threading.Semaphore,
    start_time: float,
    stop_event: threading.Event,
    target: int,
):
    signer   = wallet["signer"]
    sender   = wallet["address"]
    computer = TransactionComputer()

    is_dex    = (mode == "dex")
    is_wrap   = (mode == "wrap")
    is_intra  = (mode == "intra")
    is_cross  = (mode == "cross")
    # is_mixed: all other cases (mode == "mixed")

    intra_pool   = wallet.get("intra_pool", [])
    cross_pool   = wallet.get("cross_pool", [])
    dex_receiver = Address.new_from_bech32(config.WEGLD_USDC_SC_POOL)
    wrap_receiver = (Address.new_from_bech32(config.WEGLD_WRAP_SC_SHARD[wallet["shard"]])
                     if is_wrap else None)

    GAS_COST_RAW = GAS_MOVE_BALANCE * config.DEFAULT_GAS_PRICE   # pour rich logic

    try:
        confirmed_nonce, _ = get_account_safe(gateway_url, sender, api_semaphore)
    except Exception as e:
        print(f"[ERROR] Init failed for {wallet['name']}: {e}")
        return

    local_nonce   = confirmed_nonce
    local_intra   = 0
    local_cross   = 0
    empty_streak  = 0
    reject_streak = 0

    while True:
        if stop_event.is_set():
            break

        # -------------------------------------------------------------------
        # 1. Fetch account state
        # -------------------------------------------------------------------
        try:
            confirmed_nonce, balance = get_account_safe(gateway_url, sender, api_semaphore)
        except Exception:
            time.sleep(0.5)
            continue

        # -------------------------------------------------------------------
        # 2. Balance check + effective_value + batch_count
        # -------------------------------------------------------------------

        nonce_slots = MAX_NONCE_LOOKAHEAD - (local_nonce - confirmed_nonce)

        if is_wrap:
            # Wrap : sender paie gas + value (EGLD à wrapper), montant fixe
            wrap_cost = gas_limit_per_tx * config.DEFAULT_GAS_PRICE + amount_in
            if balance < wrap_cost:
                wait = min(1.0 * (2 ** empty_streak), 30.0)
                empty_streak += 1
                time.sleep(wait)
                continue
            empty_streak = 0
            effective_value = amount_in
            if nonce_slots <= 0:
                time.sleep(0.3)
                continue
            available_balance = max(0, balance - (local_nonce - confirmed_nonce) * wrap_cost)
            batch_count = min(batch_size, nonce_slots, max(1, available_balance // wrap_cost))

        elif is_dex or is_relayed:
            # DEX ou Relayed : le sender ne paie pas le gas (relayer) ou value=0 (DEX relayed).
            # Pour DEX non-relayed : vérifier que le sender a de l'EGLD pour le gas.
            if is_dex and not is_relayed:
                dex_gas_cost = gas_limit_per_tx * config.DEFAULT_GAS_PRICE
                if balance < dex_gas_cost:
                    wait = min(1.0 * (2 ** empty_streak), 30.0)
                    empty_streak += 1
                    time.sleep(wait)
                    continue
                empty_streak = 0
            # Sinon (relayed quelle que soit la mode, ou dex+relayed) : pas de balance check
            effective_value = 0
            if nonce_slots <= 0:
                time.sleep(0.3)
                continue
            batch_count = min(batch_size, nonce_slots)

        else:
            # MoveBalance non-relayed : rich logic
            _pending        = local_nonce - confirmed_nonce
            _avail_estimate = max(0, balance - _pending * (GAS_COST_RAW * 2))

            if balance < RICH_THRESHOLD:
                effective_value = GAS_COST_RAW
            else:
                raw = int(_avail_estimate * DRAIN_RATIO) // max(batch_size, 1)
                raw = (raw // GAS_COST_RAW) * GAS_COST_RAW
                effective_value = min(max(raw, GAS_COST_RAW), MAX_VALUE_PER_TX)

            if min_value > 0:
                effective_value = max(effective_value, min_value)

            pending_count     = local_nonce - confirmed_nonce
            available_balance = max(0, balance - pending_count * (GAS_COST_RAW + effective_value))
            if available_balance < GAS_COST_RAW + effective_value:
                wait = min(1.0 * (2 ** empty_streak), 30.0)
                empty_streak += 1
                time.sleep(wait)
                continue
            empty_streak = 0

            if nonce_slots <= 0:
                time.sleep(0.3)
                continue

            max_by_balance = available_balance // (GAS_COST_RAW + effective_value)
            batch_count = min(batch_size, nonce_slots, max_by_balance)
            if batch_count == 0:
                time.sleep(0.5)
                continue

        # -------------------------------------------------------------------
        # 3. Receiver selection
        # -------------------------------------------------------------------

        if is_wrap:
            receiver  = wrap_receiver
            use_cross = True
        elif is_dex:
            receiver  = dex_receiver
            use_cross = True
        elif is_intra:
            pool      = intra_pool or cross_pool
            receiver  = random.choice(pool) if pool else None
            use_cross = False
        elif is_cross:
            pool      = cross_pool or intra_pool
            receiver  = random.choice(pool) if pool else None
            use_cross = True
        else:  # mixed
            local_total = local_intra + local_cross
            if local_total == 0:
                use_cross = cross_ratio >= 0.5
            else:
                use_cross = (local_cross / local_total) < cross_ratio
            pool     = cross_pool if use_cross else intra_pool
            receiver = random.choice(pool) if pool else None

        if receiver is None:
            time.sleep(1.0)
            continue

        # -------------------------------------------------------------------
        # 4. Build + sign le batch
        # -------------------------------------------------------------------

        batch = []
        for j in range(batch_count):
            tx = Transaction(
                nonce=local_nonce + j,
                sender=sender,
                receiver=receiver,
                value= value if value > 0 else effective_value,                
                gas_limit=gas_limit_per_tx,
                data=dex_data if is_dex else (b"wrapEgld" if is_wrap else b""),
                chain_id=config.CHAIN_ID,
                gas_price=config.DEFAULT_GAS_PRICE,
                version=config.DEFAULT_TX_VERSION,
            )
            if is_relayed:
                tx.relayer = relayer_addr
                signing_bytes = computer.compute_bytes_for_signing(tx)
                tx.signature          = signer.sign(signing_bytes)
                tx.relayer_signature  = relayer_signer.sign(signing_bytes)
            else:
                tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
            batch.append(tx)

        # -------------------------------------------------------------------
        # 5. Dry-run
        # -------------------------------------------------------------------

        if dry_run:
            relayed_tag = "[RELAYED]" if is_relayed else ""
            kind_tag    = "WRAP" if is_wrap else ("DEX" if is_dex else ("CROSS" if use_cross else "INTRA"))
            recv_short  = receiver.to_bech32()[:20]
            for tx in batch:
                print(f"[DRY-RUN][{kind_tag}]{relayed_tag} {wallet['name']} "
                      f"shard={wallet['shard']} nonce={tx.nonce} "
                      f"value={tx.value / 1e18:.5f} EGLD → {recv_short}...")
            break

        # -------------------------------------------------------------------
        # 6. Envoi bulk
        # -------------------------------------------------------------------

        try:
            num_sent, errors = provider.send_transactions(batch)
            local_nonce += num_sent
            if num_sent < batch_count:
                first_err = next((e for e in (errors or []) if e), None)
                err_info  = f" [{first_err}]" if first_err else ""
                print(f"[WARN] {wallet['name']}: {num_sent}/{batch_count} txs accepted{err_info}")
                if num_sent == 0:
                    wait = min(2.0 * (2 ** reject_streak), 60.0)
                    reject_streak += 1
                    try:
                        confirmed_nonce, _ = get_account_safe(gateway_url, sender, api_semaphore)
                        local_nonce = confirmed_nonce
                    except Exception:
                        pass
                    time.sleep(wait)
                    continue
            reject_streak = 0
        except Exception as e:
            print(f"[WARN] {wallet['name']}: batch error: {e}")
            time.sleep(0.5)
            continue

        # -------------------------------------------------------------------
        # 7. Compteurs + stop target
        # -------------------------------------------------------------------

        if use_cross or is_dex:
            local_cross += num_sent
        else:
            local_intra += num_sent

        with lock:
            prev = counter[0]
            counter[0] += num_sent
            counter[1] += 0 if (use_cross or is_dex) else num_sent
            counter[2] += num_sent if (use_cross or is_dex) else 0
            total = counter[0]
            if target > 0 and total >= target:
                stop_event.set()

        if prev // 500 < total // 500:
            elapsed    = time.time() - start_time
            rate       = total / elapsed if elapsed > 0 else 0
            intra_pct  = counter[1] / counter[0] * 100 if counter[0] else 0
            cross_pct  = counter[2] / counter[0] * 100 if counter[0] else 0
            label2     = "dex" if is_dex else "cross"
            print(f"[INFO] {total:,} txs  {rate:.0f} tx/s  "
                  f"(intra: {counter[1]:,} {intra_pct:.0f}%  "
                  f"{label2}: {counter[2]:,} {cross_pct:.0f}%)")

        if stop_event.is_set():
            break


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="BoN Unified Stress Test — Windows A–E, modes intra/cross/mixed/dex + relayed"
    )
    parser.add_argument("--wallets-dir", action="append", dest="wallets_dirs", required=True,
                        help="Dossier .pem (répétable, un par shard)")
    parser.add_argument("--mode", required=True, choices=["intra", "cross", "mixed", "dex", "wrap"],
                        help="Type de transaction : intra / cross / mixed / dex / wrap")
    parser.add_argument("--relayer-pem", action="append", dest="relayer_pems",
                        default=[], metavar="FILE",
                        help="PEM du relayer — active RelayedV3 (répétable, un par shard)")
    parser.add_argument("--target", type=int, default=0,
                        help="Arrêter après N txs acceptées (0 = infini)")
    parser.add_argument("--max-wallets", type=int, default=100,
                        help="Max wallets à utiliser — challenge BoN : 100 max (défaut: 100)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--cross-ratio", type=float, default=0.5,
                        help="Fraction cross-shard en mode mixed (0.0–1.0, défaut: 0.5)")
    
    parser.add_argument("--value", type=int, default=0,
                        help="Montant fixe en raw à envoyer par tx (0 = rich logic)")
    parser.add_argument("--min-value", type=int, default=0, dest="min_value",
                        help="Plancher de montant en raw — aucune tx n'enverra moins (0 = désactivé). "
                             "Ex: 1 pour 1e-18 EGLD (fenêtre A), 10000000000000000 pour 0.01 EGLD (fenêtre B)")
    # DEX params
    parser.add_argument("--token-in",  default=None,
                        help="Token ESDT d'entrée (ex: WEGLD-bd4d79) — requis en mode dex")
    parser.add_argument("--token-out", default=None,
                        help="Token ESDT de sortie — requis en mode dex")
    parser.add_argument("--amount-in", type=int, default=None,
                        help="Montant ESDT en raw par tx (ex: 1000000000000000) — requis en mode dex")
    parser.add_argument("--min-out", type=int, default=1,
                        help="Montant minimum de sortie raw (défaut: 1)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Construire/signer 1 batch par wallet sans envoyer")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    args = parser.parse_args()

    # --- Validations --------------------------------------------------------

    if args.mode == "dex":
        if not args.token_in or not args.token_out or args.amount_in is None:
            print("[ERROR] Mode dex requiert --token-in, --token-out et --amount-in",
                  file=sys.stderr)
            sys.exit(1)

    if args.mode == "wrap":
        if args.amount_in is None:
            print("[ERROR] Mode wrap requiert --amount-in (EGLD raw à wrapper par tx)",
                  file=sys.stderr)
            sys.exit(1)

    if not 0.0 <= args.cross_ratio <= 1.0:
        print("[ERROR] --cross-ratio doit être entre 0.0 et 1.0", file=sys.stderr)
        sys.exit(1)

    if args.batch_size > MAX_NONCE_LOOKAHEAD:
        print(f"[WARN] --batch-size réduit à {MAX_NONCE_LOOKAHEAD - 5} (MAX_NONCE_LOOKAHEAD={MAX_NONCE_LOOKAHEAD})")
        args.batch_size = MAX_NONCE_LOOKAHEAD - 5

    # --- Relayer(s) ---------------------------------------------------------
    # Supporte 1 relayer partagé ou N relayers (un par shard).
    # Mapping : shard → (signer, addr). Si 1 seul PEM → utilisé pour tous les shards.

    is_relayed      = len(args.relayer_pems) > 0
    relayers_by_shard: dict[int, tuple] = {}   # shard → (signer, addr)
    _default_relayer = None

    for pem in args.relayer_pems:
        signer  = utils.load_signer(pem)
        addr    = utils.get_address(signer)
        shard   = compute_shard(addr)
        relayers_by_shard[shard] = (signer, addr)
        _default_relayer = (signer, addr)   # fallback si 1 seul relayer

    def get_relayer(wallet_shard: int):
        """Retourne (signer, addr) pour ce shard, ou le relayer unique en fallback."""
        if wallet_shard in relayers_by_shard:
            return relayers_by_shard[wallet_shard]
        return _default_relayer

    # --- Gas ----------------------------------------------------------------

    is_dex  = (args.mode == "dex")
    is_wrap = (args.mode == "wrap")
    if is_dex:
        base_gas = GAS_DEX_SWAP
    elif is_wrap:
        base_gas = GAS_WRAP
    else:
        base_gas = GAS_MOVE_BALANCE
    gas_limit_per_tx = base_gas + (GAS_RELAYER_OVERHEAD if is_relayed else 0)

    # --- DEX data -----------------------------------------------------------

    dex_data = None
    if is_dex:
        dex_data = build_swap_data(args.token_in, args.amount_in, args.token_out, args.min_out)

    # --- Wallets + routing --------------------------------------------------

    wallets = load_wallets(args.wallets_dirs, args.max_wallets)
    wallets = build_routing(wallets)

    by_shard: dict[int, int] = {}
    for w in wallets:
        by_shard[w["shard"]] = by_shard.get(w["shard"], 0) + 1

    # cross_ratio effectif par mode
    cross_ratio = args.cross_ratio
    if args.mode == "intra":
        cross_ratio = 0.0
    elif args.mode in ("cross", "dex", "wrap"):
        cross_ratio = 1.0

    # --- Affichage ----------------------------------------------------------

    provider = utils.get_provider(args.gateway)

    if is_relayed:
        relayer_info = "  ".join(
            f"shard-{s}={addr.to_bech32()[:16]}..." for s, (_, addr) in sorted(relayers_by_shard.items())
        ) or f"{_default_relayer[1].to_bech32()[:20]}..."
        relayed_str = f"RELAYED  {relayer_info}"
    else:
        relayed_str = "non-relayed"
    print(f"[INFO] Mode : {args.mode.upper()} | {relayed_str}")
    print(f"[INFO] {len(wallets)} wallets | par shard : {dict(sorted(by_shard.items()))}")
    print(f"[INFO] gas/tx : {gas_limit_per_tx:,} | gateway : {args.gateway}")
    if is_wrap:
        print(f"[INFO] Wrap SC shard 0 : {config.WEGLD_WRAP_SC_SHARD[0]}")
        print(f"[INFO] Wrap SC shard 1 : {config.WEGLD_WRAP_SC_SHARD[1]}")
        print(f"[INFO] Wrap SC shard 2 : {config.WEGLD_WRAP_SC_SHARD[2]}")
        print(f"[INFO] EGLD/tx : {args.amount_in / 1e18:.6f} EGLD ({args.amount_in} raw)")
    elif is_dex:
        print(f"[INFO] Pool   : {config.WEGLD_USDC_SC_POOL}")
        print(f"[INFO] Swap   : {args.token_in} → {args.token_out} | amount_in={args.amount_in} raw")
    else:
        intra_pct = (1.0 - cross_ratio) * 100
        xpct      = cross_ratio * 100
        print(f"[INFO] Ratio  : {intra_pct:.0f}% intra / {xpct:.0f}% cross")
    if args.min_value > 0:
        print(f"[INFO] Min-value : {args.min_value} raw ({args.min_value / 1e18:.18g} EGLD)")
    if args.target > 0:
        print(f"[INFO] Target : {args.target:,} txs")
    if args.dry_run:
        print("[INFO] DRY-RUN — 1 batch par wallet, aucun envoi\n")
    else:
        print("[INFO] Démarrage...\n")

    # --- Thread pool --------------------------------------------------------

    counter       = [0, 0, 0]   # [total, intra, cross/dex]
    lock          = threading.Lock()
    api_semaphore = threading.Semaphore(MAX_CONCURRENT_API_CALLS)
    stop_event    = threading.Event()
    start         = time.time()

    threads = []
    for w in wallets:
        if is_relayed:
            r_signer, r_addr = get_relayer(w["shard"])
        else:
            r_signer, r_addr = None, None
        threads.append(threading.Thread(
            target=wallet_worker,
            args=(
                w,
                args.mode,
                is_relayed,
                r_addr,
                r_signer,
                args.batch_size,
                cross_ratio,
                gas_limit_per_tx,
                args.value or 0,
                dex_data,
                args.amount_in or 0,
                args.min_value,
                args.gateway,
                provider,
                args.dry_run,
                counter,
                lock,
                api_semaphore,
                start,
                stop_event,
                args.target,
            ),
            daemon=True,
        ))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # --- Résumé final -------------------------------------------------------

    elapsed   = time.time() - start
    total     = counter[0]
    rate      = total / elapsed if elapsed > 0 else 0
    intra_pct = counter[1] / total * 100 if total else 0
    cross_pct = counter[2] / total * 100 if total else 0
    gas_cost  = total * gas_limit_per_tx * config.DEFAULT_GAS_PRICE / 1e18
    gas_payer = "relayer" if is_relayed else "senders"

    done_tag = "[TARGET ATTEINT]" if (args.target > 0 and total >= args.target) else "[DONE]"
    print(f"\n{done_tag} {total:,} txs en {elapsed:.1f}s ({rate:.0f} tx/s)")
    if not is_dex:
        print(f"         intra: {counter[1]:,} ({intra_pct:.1f}%)  "
              f"cross: {counter[2]:,} ({cross_pct:.1f}%)")
    print(f"         ~{gas_cost:.4f} EGLD gas ({gas_payer})")


if __name__ == "__main__":
    main()
