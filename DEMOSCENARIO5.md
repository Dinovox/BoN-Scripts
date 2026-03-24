# DEMO SCENARIO 5

Cross-shard MoveBalance in two windows — 500 wallets, targeting total EGLD volume.

---

## Schedule

| Window | Type                    | EGLD budget | Min value / tx | Wallets |
| ------ | ----------------------- | ----------- | -------------- | ------- |
| A      | Cross-shard MoveBalance | 2 000 EGLD  | 1 wei (1e-18)  | 500     |
| B      | Cross-shard MoveBalance | 500 EGLD    | 0.01 EGLD      | 500     |

UTC TIME
15:45 - FUNDS A
16:00 - SPAM A
16:30 - END SPAM

16:30 - FUNDS B
17:00 - SPAM B
17:30 - END SPAM A

---

## 1. GENERATE WALLETS

500 wallets split evenly across 3 shards (~167 per shard).

```bash
python generate_wallets_2.py \
  --num-shards 3 \
  --output-dir ./scen5a-wallets \
  --total 500 \
  --dry-run

  python generate_wallets_2.py \
  --num-shards 3 \
  --output-dir ./scen5b-wallets \
  --total 500 \
  --dry-run
```

---

## 2. FUND WALLETS — Window A (2 000 EGLD total)

4 EGLD per wallet × 500 wallets = 2 000 EGLD.

```bash
python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./scen5a-wallets/shard-0 \
  --amount 4 \
  --dry-run

python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./scen5a-wallets/shard-1 \
  --amount 4 \
  --dry-run

python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./scen5a-wallets/shard-2 \
  --amount 4 \
  --dry-run
```

Check balances before starting:

```bash
python check_balances_3.py \
  --wallets-dir ./scen5a-wallets/shard-0 \
  --wallets-dir ./scen5a-wallets/shard-1 \
  --wallets-dir ./scen5a-wallets/shard-2 \
  --from-wallet ./wallets/bon_supernova.pem \
  --max-wallets 500 \
  --min-egld 4 \
  --dry-run
```

---

## 3. WINDOW A — Cross-shard MoveBalance (min 1 wei)

> Rich logic drains wallets dynamically.
> `--min-value 1` ensures no tx sends 0 EGLD — floor is 1 attoEGLD (1e-18).
> Script runs until wallets are empty or manually stopped.

```bash
python stress_unified.py \
  --wallets-dir ./scen5a-wallets/shard-0 \
  --wallets-dir ./scen5a-wallets/shard-1 \
  --wallets-dir ./scen5a-wallets/shard-2 \
  --mode cross \
  --max-wallets 500 \
  --min-value 1 \
  --dry-run
```

---

## 4. FUND WALLETS — Window B (500 EGLD total)

1 EGLD per wallet × 500 wallets = 500 EGLD.

> Skip if wallets still have enough balance from Window A.

```bash
python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./scen5b-wallets/shard-0 \
  --amount 1 \
  --dry-run

python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./scen5b-wallets/shard-1 \
  --amount 1 \
  --dry-run

python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./scen5b-wallets/shard-2 \
  --amount 1 \
  --dry-run
```

---

## 5. CHECK BALANCES — between windows

Top up to 1 EGLD if wallets are not already empty for Window B.

```bash
python check_balances_3.py \
  --wallets-dir ./scen5b-wallets/shard-0 \
  --wallets-dir ./scen5b-wallets/shard-1 \
  --wallets-dir ./scen5b-wallets/shard-2 \
  --from-wallet ./wallets/bon_supernova.pem \
  --max-wallets 500 \
  --min-egld 1 \
  --dry-run
```

---

## 6. WINDOW B — Cross-shard MoveBalance (min 0.01 EGLD)

> `--min-value 10000000000000000` = 0.01 EGLD raw.
> Each tx will send at least 0.01 EGLD — rich logic floor is raised accordingly.
> Script runs until wallets are empty or manually stopped.

```bash
python stress_unified.py \
  --wallets-dir ./scen5b-wallets/shard-1 \
  --wallets-dir ./scen5b-wallets/shard-2 \
  --mode cross \
  --max-wallets 500 \
  --min-value 10000000000000000 \
  --dry-run
```

---

## 7. DRAIN ALL WALLETS

Send remaining funds back after both windows.

> **Change the `--to` address or I will be RICH!**

```bash
python drain_wallets.py \
  --wallets-dir ./scen5a-wallets/shard-0 \
  --wallets-dir ./scen5a-wallets/shard-1 \
  --wallets-dir ./scen5a-wallets/shard-2 \
  --to erd1f60kcmly42f6l9v90l9f0s0rq5dja8lvcuuu7hm3hr3skyae0hgq0mfmnl \
  --dry-run


  python drain_wallets.py \
  --wallets-dir ./scen5b-wallets/shard-0 \
  --wallets-dir ./scen5b-wallets/shard-1 \
  --wallets-dir ./scen5b-wallets/shard-2 \
  --to erd1f60kcmly42f6l9v90l9f0s0rq5dja8lvcuuu7hm3hr3skyae0hgq0mfmnl \
  --dry-run
```
