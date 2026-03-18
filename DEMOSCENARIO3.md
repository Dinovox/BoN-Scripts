# DEMO SCENARIO 3

Window challenge — 1,000,000 MoveBalance transactions (intra + cross-shard).

---

## 1. GENERATE WALLETS

500 wallets split across 3 shards.

```bash
python generate_wallets_2.py \
  --num-shards 3 \
  --output-dir ./spam-wallets \
  --total 500
```

---

## 2. FUND WALLETS

> May fail due to gateway 502 or pool drops. Run each shard separately, then check balances.

```bash
python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./spam-wallets/shard-0 \
  --amount 1

python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./spam-wallets/shard-1 \
  --amount 1

python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./spam-wallets/shard-2 \
  --amount 1
```

Or all shards at once (dry-run first):

```bash
python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./spam-wallets/shard-0 \
  --wallets-dir ./spam-wallets/shard-1 \
  --wallets-dir ./spam-wallets/shard-2 \
  --amount 1 \
  --dry-run
```

---

## 3. CHECK BALANCES

> Remove `--min-egld` to display balances without topping up.

```bash
python check_balances_3.py \
  --wallets-dir ./spam-wallets/shard-0 \
  --wallets-dir ./spam-wallets/shard-1 \
  --wallets-dir ./spam-wallets/shard-2 \
  --from-wallet ./wallets/bon_supernova.pem \
  --max-wallets 500 \
  --min-egld 1
```

---

## 4. SEND TRANSACTIONS

> - May fail if the pool is full with a missed nonce gap.
> - The rich-balance strategy can lag if one shard falls behind.
> - If the script slows down, do NOT stop it — it would lose track of submitted nonces. Use `check_mempool.py` to inspect address state first.

```bash
python stress_mixed_v2.py \
  --wallets-dir ./spam-wallets/shard-0 \
  --wallets-dir ./spam-wallets/shard-1 \
  --wallets-dir ./spam-wallets/shard-2 \
  --max-wallets 500 \
  --batch-size 95
```

> Use `--cross-ratio 1` to send only cross-shard transactions (requires at least 2 `--wallets-dir`).

---

## 5. REFUND LOW BALANCES

Top up wallets that have been drained by the ping-pong strategy.

```bash
python check_balances_3.py \
  --wallets-dir ./spam-wallets/shard-0 \
  --wallets-dir ./spam-wallets/shard-1 \
  --wallets-dir ./spam-wallets/shard-2 \
  --from-wallet ./wallets/bon_supernova.pem \
  --max-wallets 500 \
  --min-egld 1
```

---

## 6. DRAIN ALL WALLETS

Send remaining funds back to a target address.

> **Change the `--to` address or I will be RICH!**

```bash
python drain_wallets.py \
  --wallets-dir ./spam-wallets/shard-0 \
  --wallets-dir ./spam-wallets/shard-1 \
  --wallets-dir ./spam-wallets/shard-2 \
  --to erd1f60kcmly42f6l9v90l9f0s0rq5dja8lvcuuu7hm3hr3skyae0hgq0mfmnl
```

---

## 7. CHECK MEMPOOL

Inspect pending transactions for any address.

```bash
python check_mempool.py \
  --address erd1nzt08ur6xvnlqv0wyp9uqcuhpgpsnz8lnj8sydsrxl9q5fjvazxslvgk7z

# Or from a PEM file
python check_mempool.py --pem ./spam-wallets/shard-0/wallet_014.pem

# Via local gateway
python check_mempool.py \
  --address erd1nzt08ur6xvnlqv0wyp9uqcuhpgpsnz8lnj8sydsrxl9q5fjvazxslvgk7z
```
