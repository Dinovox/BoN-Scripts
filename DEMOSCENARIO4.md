# DEMO SCENARIO 4

Multi-window challenge using `stress_unified.py`.

---

## Schedule

| Window | Time (UTC)    | Type                            | Target     |
| ------ | ------------- | ------------------------------- | ---------- |
| A      | 14:00 – 14:30 | Intra-shard MoveBalance         | 50,000 txs |
| B      | 14:30 – 15:30 | DEX SC calls                    | 1,000 txs  |
| C      | 15:30 – 16:00 | Cross-shard MoveBalance         | 50,000 txs |
| D      | 16:00 – 16:30 | Relayed cross-shard MoveBalance | 10,000 txs |
| E      | 16:30 – 17:00 | Relayed DEX SC calls            | 500 txs    |

---

## 0. VALIDATOR NODE RESTART

> Restart your main validator node **within 1 hour before Window A** (between 13:00 and 14:00 UTC).
> Restart again **within 1 hour after Window E** ends (between 17:00 and 18:00 UTC).
> Upload the log generated between the two restarts to the BoN platform.

---

## 1. GENERATE WALLETS

100 wallets split across 3 shards.

```bash
python generate_wallets_2.py \
  --num-shards 3 \
  --output-dir ./fork-wallets \
  --total 100 \
  --dry-run
```

---

## 2. FUND WALLETS

```bash
python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./fork-wallets/shard-0 \
  --amount 1 \
  --dry-run

python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./fork-wallets/shard-1 \
  --amount 1 \
  --dry-run

python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./fork-wallets/shard-2 \
  --amount 1 \
  --dry-run
```

---

## 3. CHECK BALANCES

You may check balance beetween each window and refunds wallets

```bash
python check_balances_3.py \
  --relayer ./wallets/relayer_shard0.pem \
  --relayer ./wallets/relayer_shard1.pem \
  --relayer ./wallets/relayer_shard2.pem \
  --from-wallet ./wallets/bon_supernova.pem \
  --max-wallets 3 \
  --min-egld 10 \
  --dry-run
```

```bash
python check_balances_3.py \
  --wallets-dir ./fork-wallets/shard-0 \
  --wallets-dir ./fork-wallets/shard-1 \
  --wallets-dir ./fork-wallets/shard-2 \
  --from-wallet ./wallets/bon_supernova.pem \
  --max-wallets 100 \
  --min-egld 1.2 \
  --dry-run
```

---

## 4. WRAP EGLD → WEGLD + DISTRIBUTE

> Required before Windows B and E (DEX swaps need WEGLD).
> Two strategies — pick one.

### Scénario A — Wrap depuis le wallet principal, puis redistribution

Wrap un gros montant depuis `bon_supernova.pem` (shard 1, utilise le SC shard 1) :

<!-- ```bash
python wrap_egld.py \
  --wallet ./wallets/bon_supernova.pem \
  --amount 5.0 \
  --dry-run
``` -->

Distribuer le WEGLD aux 100 wallets via top-up automatique :

```bash
python check_balances_3.py \
  --wallets-dir ./fork-wallets/shard-0 \
  --wallets-dir ./fork-wallets/shard-1 \
  --wallets-dir ./fork-wallets/shard-2 \
  --from-wallet ./wallets/bon_supernova.pem \
  --max-wallets 100 \
  --min-esdt WEGLD-bd4d79 0.2 \
  --dry-run
```

---

### Scénario B — Wrap sur chaque wallet individuellement

Chaque wallet wrappe ses propres EGLD — wrap SC auto-sélectionné selon son shard :

<!--
mode spam (pas bon pour juste wrap sur chaque wallet)
 ```bash
python stress_unified.py \
  --wallets-dir ./fork-wallets/shard-0 \
  --wallets-dir ./fork-wallets/shard-1 \
  --wallets-dir ./fork-wallets/shard-2 \
  --mode wrap \
  --amount-in 20000000000000000 \
  --max-wallets 100 \
  --target 100 \
  --dry-run
``` -->

> `--amount-in` = EGLD raw à wrapper par tx (20000000000000000 = 0.02 EGLD).
> `--target 100` = 1 wrap par wallet. Augmenter si plusieurs swaps prévus par wallet.

---

Vérifier les balances WEGLD (commun aux deux scénarios) :

```bash
python check_balances_3.py \
  --wallets-dir ./fork-wallets/shard-0 \
  --wallets-dir ./fork-wallets/shard-1 \
  --wallets-dir ./fork-wallets/shard-2 \
  --max-wallets 100 \
  --token WEGLD-bd4d79
```

---

## 5. WINDOW A — Intra-shard MoveBalance (14:00 – 14:30)

> Target: 50,000 txs. Script stops automatically when target is reached.

```bash
python stress_unified.py \
  --wallets-dir ./fork-wallets/shard-0 \
  --wallets-dir ./fork-wallets/shard-1 \
  --wallets-dir ./fork-wallets/shard-2 \
  --mode intra \
  --max-wallets 100 \
  --target 60000 \
  --dry-run
```

---

## 6. WINDOW B — DEX SC Calls (14:30 – 15:30)

> Target: 1,000 txs. Wallets must hold enough `--token-in` ESDT.
> **Update `--token-in` and `--token-out` with the actual BoN token IDs before running.**

```bash
python stress_unified.py \
--gateway http://192.168.1.23:8079 \
  --wallets-dir ./fork-wallets/shard-0 \
  --wallets-dir ./fork-wallets/shard-1 \
  --wallets-dir ./fork-wallets/shard-2 \
  --mode dex \
  --token-in WEGLD-bd4d79 \
  --token-out USDC-c76f1f \
  --amount-in 1000000000000000 \
  --max-wallets 100 \
  --target 1000 \
  --dry-run
```

---

## 7. WINDOW C — Cross-shard MoveBalance (15:30 – 16:00)

> Target: 50,000 txs. Requires wallets on at least 2 shards.

```bash
python stress_unified.py \
  --wallets-dir ./fork-wallets/shard-0 \
  --wallets-dir ./fork-wallets/shard-1 \
  --wallets-dir ./fork-wallets/shard-2 \
  --mode cross \
  --max-wallets 100 \
  --target 50000 \
  --dry-run
```

---

## 8. WINDOW D — Relayed Cross-shard MoveBalance (16:00 – 16:30)

> Target: 10,000 txs. Relayer pays gas — sender wallets need no EGLD.

```bash
python stress_unified.py \
  --wallets-dir ./fork-wallets/shard-0 \
  --wallets-dir ./fork-wallets/shard-1 \
  --wallets-dir ./fork-wallets/shard-2 \
  --mode cross \
  --relayer-pem ./wallets/relayer_shard0.pem \
  --relayer-pem ./wallets/relayer_shard1.pem \
  --relayer-pem ./wallets/relayer_shard2.pem \
  --max-wallets 100 \
  --target 11000 \
  --value 0.00001
  --dry-run
```

25 279 000
1 000 000 000
340 000 000

---

## 9. WINDOW E — Relayed DEX SC Calls (16:30 – 17:00)

> Target: 500 txs. Relayer pays gas — sender wallets only need ESDT tokens.
> **Update `--token-in` and `--token-out` with the actual BoN token IDs before running.**

```bash
python stress_unified.py \
  --wallets-dir ./fork-wallets/shard-0 \
  --wallets-dir ./fork-wallets/shard-1 \
  --wallets-dir ./fork-wallets/shard-2 \
  --mode dex \
  --relayer-pem ./wallets/relayer_shard0.pem \
  --relayer-pem ./wallets/relayer_shard1.pem \
  --relayer-pem ./wallets/relayer_shard2.pem \
  --token-in WEGLD-bd4d79 \
  --token-out USDC-c76f1f \
  --amount-in 1000000000000000 \
  --max-wallets 100 \
  --target 500 \
  --dry-run
```

---

## 10. DRAIN ALL WALLETS

Send remaining funds back to a target address.

> **Change the `--to` address or I will be RICH!**

```bash
python drain_wallets.py \
  --wallets-dir ./fork-wallets/shard-0 \
  --wallets-dir ./fork-wallets/shard-1 \
  --wallets-dir ./fork-wallets/shard-2 \
  --to erd1f60kcmly42f6l9v90l9f0s0rq5dja8lvcuuu7hm3hr3skyae0hgq0mfmnl \
  --dry-run
```

---

## 10. CHECK MEMPOOL

Inspect pending transactions for any address.

```bash
python check_mempool.py \
  --address erd1nzt08ur6xvnlqv0wyp9uqcuhpgpsnz8lnj8sydsrxl9q5fjvazxslvgk7z

# Or from a PEM file
python check_mempool.py --pem ./fork-wallets/shard-0/wallet_014.pem
```
