#DEMO SCENARIO day one
https://www.youtube.com/watch?v=0M6xAvy7Ef8

#GENERATE WALLETS

python generate_wallets.py \
--num-shards 3 --output-dir ./demo-wallets \
--target-per-shard 166

#FUNDS WALLETS

python fund_wallets.py \
--from-wallet ./wallets/bon_supernova.pem \
--wallets-dir ./demo-wallets/shard-0 \
--wallets-dir ./demo-wallets/shard-1 \
--wallets-dir ./demo-wallets/shard-2 \
--amount 0.1 \
--max-wallets 500 \
--dry-run

#CHECK BALANCES

python check_balances.py \
 --wallets-dir ./demo-wallets/shard-0 \
 --wallets-dir ./demo-wallets/shard-1 \
 --wallets-dir ./demo-wallets/shard-2 \
 --from-wallet ./wallets/bon_supernova.pem \
 --max-wallets 500 \
 --min-egld 0.1 \
 --dry-run

#SEND TRANSACTIONS

python stress_burn_egld.py \
--wallets-dir ./demo-wallets/shard-0 \
--wallets-dir ./demo-wallets/shard-1 \
--wallets-dir ./demo-wallets/shard-2 \
--max-wallets 500 \
--batch-size 95 \
--dry-run
