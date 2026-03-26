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
import json
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


# ---------------------------------------------------------------------------
# GasWar Monitor
# ---------------------------------------------------------------------------

class GasWarMonitor:
    """Thread background qui poll les endpoints PPU par shard et expose
    le gas price cible selon la stratégie choisie."""

    PPU_URL = "https://api.battleofnodes.com/transactions/ppu/{shard}"

    def __init__(self, strategy: str = "faster", min_price: int = 1_000_000_000,
                 max_price: int = 3_000_000_000, poll_interval: float = 5.0):
        self.strategy      = strategy       # "fast" | "faster" | "auto" (faster×1.1)
        self.min_price     = min_price
        self.max_price     = max_price
        self.poll_interval = poll_interval
        self._prices: dict[int, dict] = {}  # shard → {"fast": int, "faster": int}
        self._lock   = threading.Lock()
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="gaswar-monitor")

    def start(self):
        self._fetch_all()          # lecture synchrone avant le 1er batch
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _fetch_all(self):
        for shard in range(NUM_SHARDS):
            try:
                r = requests.get(self.PPU_URL.format(shard=shard), timeout=3)
                if r.status_code != 200:
                    self._bump_shard(shard, f"HTTP {r.status_code}")
                    continue
                data = r.json()
                if "data" in data:
                    data = data["data"]
                with self._lock:
                    self._prices[shard] = {
                        "fast":   int(data.get("fast",   config.DEFAULT_GAS_PRICE)),
                        "faster": int(data.get("faster", config.DEFAULT_GAS_PRICE)),
                    }
            except Exception as e:
                self._bump_shard(shard, str(e))

    def _bump_shard(self, shard: int, reason: str):
        """Augmente les prix PPU du shard de 5% (plafonné à max_price) suite à une erreur API."""
        with self._lock:
            prev = self._prices.get(shard)
        if prev is None:
            return
        new_fast   = min(int(prev["fast"]   * 1.05), self.max_price)
        new_faster = min(int(prev["faster"] * 1.05), self.max_price)
        with self._lock:
            self._prices[shard] = {"fast": new_fast, "faster": new_faster}
        print(f"[GASWAR] s{shard} PPU indispo ({reason}) → "
              f"fast={new_fast/1e9:.2f} faster={new_faster/1e9:.2f} GWei (+5%)")

    def _run(self):
        while not self._stop.is_set():
            self._stop.wait(self.poll_interval)
            if not self._stop.is_set():
                self._fetch_all()

    def get_gas_price(self, shard: int) -> int:
        with self._lock:
            data = self._prices.get(shard, {})
        if not data:
            return config.DEFAULT_GAS_PRICE
        if self.strategy == "fast":
            price = data["fast"]
        elif self.strategy == "faster":
            price = data["faster"]
        else:  # "auto" : faster + 10 %
            price = int(data["faster"] * 1.1)
        return min(max(price, self.min_price), self.max_price)

    def get_raw_ppu(self, shard: int) -> str:
        with self._lock:
            d = self._prices.get(shard)
        if not d:
            return "?"
        return f"ppu={d['faster']/1e9:.2f}"

    def display(self) -> str:
        with self._lock:
            parts = [
                f"s{s}: fast={d['fast']/1e9:.2f} faster={d['faster']/1e9:.2f} GWei"
                for s, d in sorted(self._prices.items())
            ]
        return "  |  ".join(parts) if parts else "initialisation..."


def get_account_safe(gateway_url: str, address, api_semaphore) -> tuple[int, int]:
    """Retourne (nonce_confirmé, balance_raw) via HTTP direct."""
    url = f"{gateway_url.rstrip('/')}/address/{address.to_bech32()}"
    with api_semaphore:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", {}).get("account", {})
        return int(data.get("nonce", 0)), int(data.get("balance", 0))


def get_esdt_safe(gateway_url: str, address, api_semaphore,
                  tokens: list) -> dict:
    """Retourne {token_id: balance_raw} pour les tokens demandés. {} sur erreur."""
    url = f"{gateway_url.rstrip('/')}/address/{address.to_bech32()}/esdt"
    try:
        with api_semaphore:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            esdts = r.json().get("data", {}).get("esdts", {})
        return {t: int(esdts[t]["balance"]) for t in tokens if t in esdts}
    except Exception:
        return {}


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


# ---------------------------------------------------------------------------
# Forwarder / Challenge 4 — Contract Storm
# ---------------------------------------------------------------------------

WEGLD_TOKEN_ID      = "WEGLD-bd4d79"
USDC_TOKEN_ID       = "USDC-c76f1f"
GAS_FORWARDER_CALL  = 70_000_000
GAS_FORWARDER_DRAIN = 10_000_000

CALL_TYPES_ALL   = ["blindSync", "blindAsyncV1", "blindAsyncV2", "blindTransfExec"]
CALL_TYPES_ASYNC = ["blindAsyncV1", "blindAsyncV2", "blindTransfExec"]  # shards != 1


def build_forwarder_data(token_in: str, amount_in_raw: int, call_type: str,
                          dex_addr_hex: str, token_out: str, min_out: int = 1) -> bytes:
    """ESDTTransfer + appel endpoint forwarder (blindSync / blindAsyncV1 / etc.)."""
    data = "@".join([
        "ESDTTransfer",
        encode_hex(token_in),
        encode_amount_hex(amount_in_raw),
        encode_hex(call_type),             # endpoint du forwarder
        dex_addr_hex,                      # ManagedAddress destination (32 bytes raw hex)
        encode_hex("swapTokensFixedInput"), # FunctionCall.function_name
        encode_hex(token_out),             # FunctionCall.arg[0] : token de retour
        encode_amount_hex(min_out),        # FunctionCall.arg[1] : montant minimum
    ])
    return data.encode()


def build_drain_data(token_id: str) -> bytes:
    """Appel drain@<token_hex>@00 sur le contrat forwarder."""
    return f"drain@{encode_hex(token_id)}@00".encode()


GAS_ESDT_TRANSFER       = 500_000    # ESDTTransfer simple (1 token)
GAS_MULTI_ESDT_TRANSFER = 800_000    # MultiESDTNFTTransfer (≥2 tokens)


def build_esdt_transfer_data(token_id: str, amount: int) -> bytes:
    """ESDTTransfer simple : ESDTTransfer@<token_hex>@<amount_hex>"""
    return f"ESDTTransfer@{encode_hex(token_id)}@{encode_amount_hex(amount)}".encode()


def build_multi_esdt_transfer_data(receiver_hex: str, tokens: list) -> bytes:
    """MultiESDTNFTTransfer@<receiver_hex>@<num>@<tok1_hex>@00@<amt1>@<tok2_hex>@00@<amt2>…
    Pour tokens fongibles, nonce = 00.
    Le champ receiver de la Transaction doit être sender (deployer).
    """
    parts = ["MultiESDTNFTTransfer", receiver_hex, encode_amount_hex(len(tokens))]
    for token_id, amount in tokens:
        parts += [encode_hex(token_id), "00", encode_amount_hex(amount)]
    return "@".join(parts).encode()


def load_forwarders(path: str) -> dict:
    """Charge forwarders.json → {shard_int: {"contract": Address, "deployer_pem": str}}"""
    with open(path) as f:
        raw = json.load(f)
    result = {}
    for key, info in raw.items():
        shard = int(key.split("-")[1])
        result[shard] = {
            "contract":     Address.new_from_bech32(info["contract"]),
            "deployer_pem": info.get("deployer_pem", ""),
        }
    return result


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
# Contract workers (Challenge 4)
# ---------------------------------------------------------------------------

def contract_wallet_worker(
    wallet: dict,
    call_type: str,           # un des CALL_TYPES_ALL
    forwarder_addr,           # Address du forwarder pour ce shard
    token_in: str,            # token d'entrée normal (WEGLD)
    token_out: str,           # token de sortie normal (USDC)
    amount_in: int,           # montant raw direction normale
    dex_addr_hex: str,        # 32-byte hex du DEX pair
    alt_amount_in: int,       # montant raw direction alternée 0=désactivé (USDC→WEGLD)
    batch_size: int,
    gateway_url: str,
    provider,
    dry_run: bool,
    counter: list,            # [total]
    type_counter: dict,       # {call_type: count} — partagé, protégé par lock
    lock: threading.Lock,
    api_semaphore: threading.Semaphore,
    start_time: float,
    stop_event: threading.Event,
    target: int,
    gas_war_monitor: "GasWarMonitor | None" = None,
):
    signer         = wallet["signer"]
    sender         = wallet["address"]
    computer       = TransactionComputer()
    empty_streak   = 0
    reject_streak  = 0
    esdt_cache: dict = {}
    need_esdt_fetch  = True   # fetch au démarrage et après chaque drain/batch

    ESDT_CACHE_TTL   = 30.0   # re-fetch de force après 30s même sans trigger
    last_esdt_fetch  = 0.0

    try:
        confirmed_nonce, _ = get_account_safe(gateway_url, sender, api_semaphore)
    except Exception as e:
        print(f"[ERROR] Init failed for {wallet['name']}: {e}")
        return

    local_nonce = confirmed_nonce

    while True:
        if stop_event.is_set():
            break

        # --- 1. Fetch ESDT si nécessaire ---
        now = time.time()
        if need_esdt_fetch or (now - last_esdt_fetch) > ESDT_CACHE_TTL:
            esdt_cache      = get_esdt_safe(gateway_url, sender, api_semaphore,
                                            [token_in, token_out])
            need_esdt_fetch = False
            last_esdt_fetch = time.time()

        wegld_bal = esdt_cache.get(token_in, 0)
        usdc_bal  = esdt_cache.get(token_out, 0)

        # --- 2. Décider la direction selon balances réelles ---
        if wegld_bal >= amount_in:
            cur_token_in, cur_token_out, cur_amount = token_in, token_out, amount_in
        elif alt_amount_in > 0 and usdc_bal >= alt_amount_in:
            cur_token_in, cur_token_out, cur_amount = token_out, token_in, alt_amount_in
        else:
            # Tokens insuffisants — drain géré par le deployer (onlyOwner).
            # Attendre que les callbacks async ramènent les tokens ou que le drain deployer s'exécute.
            wait = min(2.0 * (2 ** empty_streak), 30.0)
            empty_streak += 1
            if not dry_run:
                print(f"[WAIT] {wallet['name']} shard={wallet['shard']} "
                      f"WEGLD={wegld_bal/1e18:.4f} USDC={usdc_bal/1e6:.4f} — "
                      f"attente drain deployer ({wait:.0f}s)")
            time.sleep(wait)
            need_esdt_fetch = True
            continue

        tx_data = build_forwarder_data(cur_token_in, cur_amount, call_type,
                                       dex_addr_hex, cur_token_out)

        # --- 3. Vérifier EGLD pour gas ---
        try:
            confirmed_nonce, balance = get_account_safe(gateway_url, sender, api_semaphore)
        except Exception:
            time.sleep(0.5)
            continue

        gas_price       = (gas_war_monitor.get_gas_price(wallet["shard"])
                           if gas_war_monitor else config.DEFAULT_GAS_PRICE)
        gas_cost_per_tx = GAS_FORWARDER_CALL * gas_price

        if balance < gas_cost_per_tx:
            wait = min(1.0 * (2 ** empty_streak), 30.0)
            empty_streak += 1
            time.sleep(wait)
            continue
        empty_streak = 0

        nonce_slots = MAX_NONCE_LOOKAHEAD - (local_nonce - confirmed_nonce)
        if nonce_slots <= 0:
            time.sleep(0.3)
            continue

        batch_count = min(batch_size, nonce_slots)

        batch = []
        for j in range(batch_count):
            tx = Transaction(
                nonce=local_nonce + j,
                sender=sender,
                receiver=forwarder_addr,
                value=0,
                gas_limit=GAS_FORWARDER_CALL,
                data=tx_data,
                chain_id=config.CHAIN_ID,
                gas_price=gas_price,
                version=config.DEFAULT_TX_VERSION,
            )
            tx.signature = signer.sign(computer.compute_bytes_for_signing(tx))
            batch.append(tx)

        if dry_run:
            fwd_short = forwarder_addr.to_bech32()[:20]
            for tx in batch:
                print(f"[DRY-RUN][CONTRACT][{call_type}] {wallet['name']} "
                      f"shard={wallet['shard']} nonce={tx.nonce} → {fwd_short}...")
            break

        try:
            num_sent, errors = provider.send_transactions(batch)
            local_nonce += num_sent
            if num_sent < batch_count:
                first_err = next((e for e in (errors or []) if e), None)
                err_info  = f" [{first_err}]" if first_err else ""
                print(f"[WARN] {wallet['name']}: {num_sent}/{batch_count} txs{err_info}")
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

        need_esdt_fetch = True   # les balances ESDT ont changé après le batch

        with lock:
            prev  = counter[0]
            counter[0] += num_sent
            type_counter[call_type] = type_counter.get(call_type, 0) + num_sent
            total = counter[0]
            if target > 0 and total >= target:
                stop_event.set()

        if prev // 200 < total // 200:
            elapsed  = time.time() - start_time
            rate     = total / elapsed if elapsed > 0 else 0
            type_str = "  ".join(
                f"{t}:{type_counter.get(t, 0):,}" for t in CALL_TYPES_ALL
            )
            gw_tag   = "gaswar" if gas_war_monitor else "fixed"
            raw_ppu  = (f" {gas_war_monitor.get_raw_ppu(wallet['shard'])}"
                        if gas_war_monitor else "")
            gw_info  = f"  [{gw_tag} s{wallet['shard']} {gas_price/1e9:.2f} GWei{raw_ppu}]"
            print(f"[INFO] {total:,} txs  {rate:.0f} tx/s  [{type_str}]{gw_info}")

        if stop_event.is_set():
            break


def _redistribute_esdt(
    shard: int,
    deployer: dict,
    drained_tokens: list,    # [(token_id, bal_from_forwarder), ...]
    recipients: list,        # wallets scen6 du même shard [{address, name, …}, …]
    gateway_url: str,
    provider,
    api_semaphore: threading.Semaphore,
    computer: TransactionComputer,
):
    """Redistribue équitablement les ESDT drainés aux wallets scen6 du même shard.
    Utilise MultiESDTNFTTransfer si ≥2 tokens (économie de N txs), ESDTTransfer sinon.
    """
    if not recipients:
        return

    # Attendre quelques blocs que les drain txs soient confirmées
    time.sleep(4.0)

    token_ids = [t for t, _ in drained_tokens]
    deployer_esdt = get_esdt_safe(gateway_url, deployer["address"], api_semaphore, token_ids)
    try:
        nonce, balance = get_account_safe(gateway_url, deployer["address"], api_semaphore)
    except Exception:
        return

    # Parts par wallet (arrondi inférieur, reste gardé par le deployer)
    per_wallet: dict[str, int] = {}
    for token_id in token_ids:
        bal = deployer_esdt.get(token_id, 0)
        if bal > 0:
            share = bal // len(recipients)
            if share > 0:
                per_wallet[token_id] = share

    if not per_wallet:
        return

    use_multi = len(per_wallet) >= 2
    gas_per_tx = GAS_MULTI_ESDT_TRANSFER if use_multi else GAS_ESDT_TRANSFER

    total_gas_needed = gas_per_tx * config.DEFAULT_GAS_PRICE * len(recipients)
    if balance < total_gas_needed:
        print(f"[REDIST] shard-{shard}: EGLD insuffisant ({balance/1e18:.4f} dispo, "
              f"{total_gas_needed/1e18:.4f} requis) — skip")
        return

    txs_to_send = []
    for recipient in recipients:
        if use_multi:
            try:
                recv_hex = bytes(recipient["address"].pubkey).hex()
            except AttributeError:
                recv_hex = recipient["address"].to_hex()
            data = build_multi_esdt_transfer_data(recv_hex, list(per_wallet.items()))
            tx = Transaction(
                nonce=nonce + len(txs_to_send),
                sender=deployer["address"],
                receiver=deployer["address"],  # MultiESDTNFTTransfer : sender == receiver
                value=0,
                gas_limit=GAS_MULTI_ESDT_TRANSFER,
                data=data,
                chain_id=config.CHAIN_ID,
                gas_price=config.DEFAULT_GAS_PRICE,
                version=config.DEFAULT_TX_VERSION,
            )
        else:
            token_id, amount = next(iter(per_wallet.items()))
            tx = Transaction(
                nonce=nonce + len(txs_to_send),
                sender=deployer["address"],
                receiver=recipient["address"],
                value=0,
                gas_limit=GAS_ESDT_TRANSFER,
                data=build_esdt_transfer_data(token_id, amount),
                chain_id=config.CHAIN_ID,
                gas_price=config.DEFAULT_GAS_PRICE,
                version=config.DEFAULT_TX_VERSION,
            )
        tx.signature = deployer["signer"].sign(computer.compute_bytes_for_signing(tx))
        txs_to_send.append(tx)

    try:
        sent, _ = provider.send_transactions(txs_to_send)
        tokens_str = "  ".join(f"{t}={a}" for t, a in per_wallet.items())
        mode_str = "MultiESDT" if use_multi else "ESDT"
        print(f"[REDIST] shard-{shard}: {sent}/{len(txs_to_send)} "
              f"{mode_str}Transfer ({tokens_str} × {len(recipients)} wallets)")
    except Exception as e:
        print(f"[REDIST] shard-{shard}: erreur redistribution: {e}")


def forwarder_drain_thread(
    forwarders: dict,
    drain_interval: float,
    gateway_url: str,
    provider,
    stop_event: threading.Event,
    api_semaphore: threading.Semaphore,
    wallets_by_shard: "dict[int, list[dict]] | None" = None,  # scen6 wallets per shard
    drain_signers: "dict[int, dict] | None" = None,
    direction_ref: "list[int] | None" = None,
):
    """Thread daemon qui drain périodiquement WEGLD + USDC des forwarder contracts,
    puis redistribue les tokens drainés aux wallets scen6 du même shard.
    """
    computer  = TransactionComputer()
    deployers = {}

    for shard, info in forwarders.items():
        # Priorité : drain_signers passés en paramètre > deployer_pem du JSON
        pre = (drain_signers or {}).get(shard)
        if pre:
            deployers[shard] = {**pre, "contract": info["contract"]}
            continue
        pem = info.get("deployer_pem", "")
        if not pem:
            continue
        try:
            signer = utils.load_signer(pem)
            addr   = utils.get_address(signer)
            deployers[shard] = {"signer": signer, "address": addr,
                                "contract": info["contract"]}
        except Exception as e:
            print(f"[DRAIN] Erreur chargement deployer shard-{shard}: {e}")

    if not deployers:
        print("[DRAIN] Aucun wallet de drain chargé — drain désactivé")
        return

    while not stop_event.wait(drain_interval):
        for shard, dep in deployers.items():
            fwd_addr = dep["contract"]

            # Vérifier les balances ESDT du forwarder — drainer seulement si > 0
            esdt = get_esdt_safe(gateway_url, fwd_addr, api_semaphore,
                                 [WEGLD_TOKEN_ID, USDC_TOKEN_ID])
            tokens_present = [(t, b) for t, b in esdt.items() if b > 0]
            if not tokens_present:
                continue  # rien à drainer sur ce shard

            try:
                nonce, _ = get_account_safe(gateway_url, dep["address"], api_semaphore)
            except Exception:
                continue

            txs = []
            for token_id, bal in tokens_present:
                tx = Transaction(
                    nonce=nonce + len(txs),
                    sender=dep["address"],
                    receiver=fwd_addr,
                    value=0,
                    gas_limit=GAS_FORWARDER_DRAIN,
                    data=build_drain_data(token_id),
                    chain_id=config.CHAIN_ID,
                    gas_price=config.DEFAULT_GAS_PRICE,
                    version=config.DEFAULT_TX_VERSION,
                )
                tx.signature = dep["signer"].sign(computer.compute_bytes_for_signing(tx))
                txs.append((token_id, bal, tx))

            try:
                sent, _ = provider.send_transactions([t for _, _, t in txs])
                labels = "  ".join(
                    f"{tid}={bal/1e18:.4f}" if tid == WEGLD_TOKEN_ID else f"{tid}={bal/1e6:.4f}"
                    for tid, bal, _ in txs
                )
                print(f"[DRAIN] shard-{shard}: {sent}/{len(txs)} drain ({labels})")
                if wallets_by_shard and sent > 0:
                    _redistribute_esdt(
                        shard, dep, tokens_present,
                        wallets_by_shard.get(shard, []),
                        gateway_url, provider, api_semaphore, computer,
                    )
            except Exception as e:
                print(f"[DRAIN] shard-{shard}: erreur: {e}")


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
    gas_war_monitor: "GasWarMonitor | None" = None,
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
                gas_price=(gas_war_monitor.get_gas_price(wallet["shard"])
                           if gas_war_monitor else config.DEFAULT_GAS_PRICE),
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
            cur_gas  = (gas_war_monitor.get_gas_price(wallet["shard"])
                        if gas_war_monitor else config.DEFAULT_GAS_PRICE)
            gw_tag   = "gaswar" if gas_war_monitor else "fixed"
            raw_ppu  = (f" {gas_war_monitor.get_raw_ppu(wallet['shard'])}"
                        if gas_war_monitor else "")
            gw_info  = f"  [{gw_tag} s{wallet['shard']} {cur_gas/1e9:.2f} GWei{raw_ppu}]"
            print(f"[INFO] {total:,} txs  {rate:.0f} tx/s  "
                  f"(intra: {counter[1]:,} {intra_pct:.0f}%  "
                  f"{label2}: {counter[2]:,} {cross_pct:.0f}%){gw_info}")

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
    parser.add_argument("--mode", required=True,
                        choices=["intra", "cross", "mixed", "dex", "wrap", "contract"],
                        help="Type de transaction : intra / cross / mixed / dex / wrap / contract")
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
    # Contract / Forwarder (mode contract — Challenge 4)
    parser.add_argument("--forwarders", default="forwarders.json",
                        help="JSON des forwarder contracts déployés (défaut: forwarders.json)")
    parser.add_argument("--call-type", default="cycle",
                        choices=CALL_TYPES_ALL + ["cycle"], dest="call_type",
                        help="Type d'appel forwarder ou 'cycle' pour tourner (défaut: cycle)")
    parser.add_argument("--shard1-sync-ratio", type=float, default=0.85, dest="shard1_sync_ratio",
                        help="Fraction des wallets shard-1 assignés à blindSync en mode cycle "
                             "(reste réparti sur les 3 async, défaut: 0.85)")
    parser.add_argument("--drain-interval", type=float, default=60.0, dest="drain_interval",
                        help="Secondes entre les drains auto (0 = désactivé, défaut: 60)")
    parser.add_argument("--alternate-swap", action="store_true", dest="alternate_swap",
                        help="Après chaque drain, alterne la direction du swap "
                             "(WEGLD→USDC puis USDC→WEGLD). Requiert --amount-in-alt.")
    parser.add_argument("--amount-in-alt", type=int, default=0, dest="amount_in_alt",
                        help="Montant ESDT raw pour la direction alternée (ex: 500000 = 0.5 USDC). "
                             "Requis avec --alternate-swap.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Construire/signer 1 batch par wallet sans envoyer")
    parser.add_argument("--gateway", default=config.GATEWAY_URL)
    # Gas war
    parser.add_argument("--gaswar", action="store_true",
                        help="Active le mode gas war : ajuste le gas price à chaud via les endpoints PPU")
    parser.add_argument("--gaswar-strategy", default="faster",
                        choices=["fast", "faster", "auto"], dest="gaswar_strategy",
                        help="Stratégie gas price : fast | faster | auto=faster×1.1 (défaut: faster)")
    parser.add_argument("--gaswar-poll", type=float, default=5.0, dest="gaswar_poll",
                        help="Intervalle de polling PPU en secondes (défaut: 5)")
    parser.add_argument("--gaswar-min", type=int, default=1_000_000_000, dest="gaswar_min",
                        help="Gas price minimum plancher en raw (défaut: 1_000_000_000 = 1 GWei)")
    parser.add_argument("--gaswar-max", type=int, default=3_000_000_000, dest="gaswar_max",
                        help="Gas price maximum autorisé en raw (défaut: 3_000_000_000 = 3 GWei)")
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

    if args.mode == "contract":
        if args.token_in is None:
            args.token_in = WEGLD_TOKEN_ID
        if args.token_out is None:
            args.token_out = USDC_TOKEN_ID
        if args.amount_in is None:
            args.amount_in = 10_000_000_000_000_000  # 0.01 WEGLD par défaut

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

    is_dex      = (args.mode == "dex")
    is_wrap     = (args.mode == "wrap")
    is_contract = (args.mode == "contract")
    if is_dex:
        base_gas = GAS_DEX_SWAP
    elif is_wrap:
        base_gas = GAS_WRAP
    elif is_contract:
        base_gas = GAS_FORWARDER_CALL
    else:
        base_gas = GAS_MOVE_BALANCE
    gas_limit_per_tx = base_gas + (GAS_RELAYER_OVERHEAD if is_relayed else 0)

    # --- GasWar monitor -----------------------------------------------------

    gas_war_monitor: GasWarMonitor | None = None
    if args.gaswar:
        gas_war_monitor = GasWarMonitor(
            strategy=args.gaswar_strategy,
            min_price=args.gaswar_min,
            max_price=args.gaswar_max,
            poll_interval=args.gaswar_poll,
        )
        gas_war_monitor.start()
        print(f"[GASWAR] Activé — stratégie={args.gaswar_strategy}  "
              f"min={args.gaswar_min/1e9:.2f} max={args.gaswar_max/1e9:.2f} GWei  "
              f"poll={args.gaswar_poll}s  PPU initial: {gas_war_monitor.display()}")

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
    elif args.mode in ("cross", "dex", "wrap", "contract"):
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
    elif is_contract:
        print(f"[INFO] DEX pair  : {config.WEGLD_USDC_SC_POOL}")
        print(f"[INFO] Swap      : {args.token_in} → {args.token_out} | amount={args.amount_in} raw")
        print(f"[INFO] Call type : {args.call_type}")
        if args.alternate_swap and args.amount_in_alt > 0:
            print(f"[INFO] Drain réactif : quand WEGLD < {args.amount_in} raw → drain + USDC→WEGLD ({args.amount_in_alt} raw)")
        else:
            print(f"[INFO] Drain réactif : quand WEGLD < {args.amount_in} raw → drain uniquement")
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

    if is_contract:
        # ---- Contract mode (Challenge 4) -----------------------------------
        try:
            forwarders = load_forwarders(args.forwarders)
        except Exception as e:
            print(f"[ERROR] Impossible de charger {args.forwarders}: {e}", file=sys.stderr)
            sys.exit(1)

        # 32-byte hex de l'adresse DEX pair (argument ManagedAddress dans le contrat)
        _dex = Address.new_from_bech32(config.WEGLD_USDC_SC_POOL)
        try:
            dex_addr_hex = _dex.to_hex()
        except AttributeError:
            dex_addr_hex = bytes(_dex.pubkey).hex()

        alt_amount_in = args.amount_in_alt if args.alternate_swap else 0

        type_counter: dict[str, int] = {t: 0 for t in CALL_TYPES_ALL}
        threads      = []
        wallet_idx   = 0
        shard_idx: dict[int, int] = {}  # compteur par shard pour le cycle

        # Pré-calcul du seuil blindSync pour shard-1
        shard1_total    = sum(1 for w in wallets if w["shard"] == 1)
        shard1_sync_cap = max(1, int(shard1_total * args.shard1_sync_ratio))

        for w in wallets:
            shard = w["shard"]
            if shard not in forwarders:
                print(f"[WARN] {w['name']}: pas de forwarder pour shard {shard} — ignoré")
                wallet_idx += 1
                continue

            forwarder_addr = forwarders[shard]["contract"]

            # Assigne le type d'appel
            if args.call_type == "cycle":
                idx_in_shard = shard_idx.get(shard, 0)
                if shard == 1:
                    # 85% (shard1_sync_cap) → blindSync, reste → cycle async
                    if idx_in_shard < shard1_sync_cap:
                        assigned = "blindSync"
                    else:
                        async_idx = idx_in_shard - shard1_sync_cap
                        assigned = CALL_TYPES_ASYNC[async_idx % len(CALL_TYPES_ASYNC)]
                else:
                    assigned = CALL_TYPES_ASYNC[idx_in_shard % len(CALL_TYPES_ASYNC)]
                shard_idx[shard] = idx_in_shard + 1
            elif args.call_type == "blindSync" and shard != 1:
                print(f"[WARN] {w['name']}: blindSync requiert shard 1 (wallet shard {shard}) — ignoré")
                wallet_idx += 1
                continue
            else:
                assigned = args.call_type

            threads.append(threading.Thread(
                target=contract_wallet_worker,
                args=(
                    w, assigned, forwarder_addr,
                    args.token_in, args.token_out, args.amount_in, dex_addr_hex,
                    alt_amount_in,
                    args.batch_size, args.gateway, provider,
                    args.dry_run, counter, type_counter, lock,
                    api_semaphore, start, stop_event, args.target,
                    gas_war_monitor,
                ),
                daemon=True,
            ))
            wallet_idx += 1

        # Thread de drain deployer : vérifie les balances ESDT du forwarder et drain si > 0
        # (onlyOwner — seul le deployer peut appeler drain, pas les scen6 wallets)
        # Après drain, redistribue les tokens aux scen6 wallets du même shard.
        wallets_by_shard: dict[int, list] = {}
        for w in wallets:
            wallets_by_shard.setdefault(w["shard"], []).append(w)

        if args.drain_interval > 0 and not args.dry_run:
            effective_drain_interval = max(60.0, args.drain_interval)
            threading.Thread(
                target=forwarder_drain_thread,
                args=(forwarders, effective_drain_interval, args.gateway,
                      provider, stop_event, api_semaphore,
                      wallets_by_shard),
                daemon=True,
                name="forwarder-drain",
            ).start()

        for t in threads:
            t.start()
        for t in threads:
            t.join()

    else:
        # ---- Modes classiques (intra / cross / mixed / dex / wrap) ---------
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
                    gas_war_monitor,
                ),
                daemon=True,
            ))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

    if gas_war_monitor:
        gas_war_monitor.stop()

    # --- Résumé final -------------------------------------------------------

    elapsed   = time.time() - start
    total     = counter[0]
    rate      = total / elapsed if elapsed > 0 else 0
    intra_pct = counter[1] / total * 100 if total else 0
    cross_pct = counter[2] / total * 100 if total else 0
    gas_payer = "relayer" if is_relayed else "senders"

    done_tag = "[TARGET ATTEINT]" if (args.target > 0 and total >= args.target) else "[DONE]"
    print(f"\n{done_tag} {total:,} txs en {elapsed:.1f}s ({rate:.0f} tx/s)")
    if is_contract:
        for t in CALL_TYPES_ALL:
            cnt = type_counter.get(t, 0)
            ok  = "✓" if cnt >= 300 else "✗"
            print(f"         {ok} {t}: {cnt:,} (min 300)")
    elif not is_dex:
        print(f"         intra: {counter[1]:,} ({intra_pct:.1f}%)  "
              f"cross: {counter[2]:,} ({cross_pct:.1f}%)")
    if gas_war_monitor:
        print(f"         gas price variable [gaswar/{args.gaswar_strategy}] "
              f"— PPU final : {gas_war_monitor.display()}")
    else:
        gas_cost = total * gas_limit_per_tx * config.DEFAULT_GAS_PRICE / 1e18
        print(f"         ~{gas_cost:.4f} EGLD gas ({gas_payer})")


if __name__ == "__main__":
    main()
