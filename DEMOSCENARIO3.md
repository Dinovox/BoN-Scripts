#### DEMO SCENARIO

used from 24h window

##### GENERATE WALLETS

# 500 WALLE slpited into 3 Shards

python generate_wallets_2.py \
--num-shards 3 --output-dir ./spam-wallets \
--total 500

##### FUNDS WALLETS

# this may fail try run each commands once then check ballance

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

# you may try to feed all wallets in one time if fail run check balance

python fund_wallets_2.py \
 --from-wallet ./wallets/bon_supernova.pem \
 --wallets-dir ./spam-wallets/shard-0 \
 --wallets-dir ./spam-wallets/shard-1 \
 --wallets-dir ./spam-wallets/shard-2 \
--amount 2 --gateway 192.168.1.23:8079 \
--dry-run

##### CHECK BALANCES

# inital funding may fail because of gateway 502 or pool drop

# remove --min-egld 1 to check balance without funding

python check_balances_3.py \
 --wallets-dir ./spam-wallets/shard-0 \
 --wallets-dir ./spam-wallets/shard-1 \
 --wallets-dir ./spam-wallets/shard-2 \
 --from-wallet ./wallets/bon_supernova.pem \
 --max-wallets 500 \
 --min-egld 1

##### SEND TRANSACTIONS

# This part is still challenging work quite well under normal loads.

# may fail once pool is full with missed nonce.

# rich balance strat could fail if one of the shard is left behing

# may send only few dust to prevent bad rebalance instead of gas price as min

# script may slow down at some point dont stop it or it will loose track of submitted nonce under pressure. use check_mempool to see state of some addresss first

python stress_mixed_v2.py \
--wallets-dir ./spam-wallets/shard-0 \
--wallets-dir ./spam-wallets/shard-1 \
--wallets-dir ./spam-wallets/shard-2 \
--max-wallets 500 \
--batch-size 95

# use --cross-ratio 1 to send only cross-shard tx (requier at least 2 wallet-dir)

#### BALANCE REFUND

# any time you may refund lowe balance if ping pong strategy fail

python check_balances_3.py \
 --wallets-dir ./spam-wallets/shard-0 \
 --wallets-dir ./spam-wallets/shard-1 \
 --wallets-dir ./spam-wallets/shard-2 \
 --from-wallet ./wallets/bon_supernova.pem \
 --max-wallets 500 \
 --min-egld 1

#### EMPTY ALL WALLETS

# from challenges sending them to a temporay wallet

# if you are using this script change this address or i will be RICH !

python drain_wallets.py \
 --wallets-dir ./spam-wallets/shard-0 \
 --wallets-dir ./spam-wallets/shard-1 \
 --wallets-dir ./spam-wallets/shard-2 \
 --to erd1f60kcmly42f6l9v90l9f0s0rq5dja8lvcuuu7hm3hr3skyae0hgq0mfmnl

#### CHECK AN ADDRESS POOL OF TRANSACTION

#### Work in progress

python check_mempool.py --address erd1nzt08ur6xvnlqv0wyp9uqcuhpgpsnz8lnj8sydsrxl9q5fjvazxslvgk7z
