#DEMO SCENARIO day two... did not got good because of squad desynchro :D
https://www.youtube.com/watch?v=b5-rqOK1_Bo

#GENERATE WALLETS

python generate_wallets_2.py \
--num-shards 3 --output-dir ./win4-wallets \
--total 500 \
--dry-run

#FUNDS WALLETS

tryed to fund in 3 time to optimize and got messy most of wallet were empty

python fund_wallets_2.py \
 --from-wallet ./wallets/bon_supernova.pem \
 --wallets-dir ./win4-wallets/shard-0 \
 --amount 1

python fund_wallets_2.py \
 --from-wallet ./wallets/bon_supernova.pem \
 --wallets-dir ./win4-wallets/shard-1 \
 --amount 1

python fund_wallets_2.py \
 --from-wallet ./wallets/bon_supernova.pem \
 --wallets-dir ./win4-wallets/shard-2 \
 --amount 1

#window 3 got better results but at this stage our proxy was still working
python fund_wallets_2.py \
 --from-wallet ./wallets/bon_supernova.pem \
 --wallets-dir ./win3-wallets/shard-0 \
 --wallets-dir ./win3-wallets/shard-& \
 --wallets-dir ./win3-wallets/shard-2 \
 --amount 1

#CHECK BALANCES

still got some bug related to threads here we need to improve but we also need more time and more testing :D

python check_balances.py \
 --wallets-dir ./win4-wallets/shard-0 \
 --from-wallet ./wallets/bon_supernova.pem \
 --max-wallets 500 \
 --min-egld 1 \
 --dry-run

python check_balances.py \
 --wallets-dir ./win4-wallets/shard-1 \
 --from-wallet ./wallets/bon_supernova.pem \
 --max-wallets 500 \
 --min-egld 1 \
 --dry-run

python check_balances.py \
 --wallets-dir ./win4-wallets/shard-2 \
 --from-wallet ./wallets/bon_supernova.pem \
 --max-wallets 500 \
 --min-egld 1 \
 --dry-run

python check_balances.py \
 --wallets-dir ./win4-wallets/shard-0 \
 --wallets-dir ./win4-wallets/shard-1 \
 --wallets-dir ./win4-wallets/shard-2 \
 --from-wallet ./wallets/bon_supernova.pem \
 --max-wallets 500 \
 --gateway http://192.168.1.23:8079
--dry-run

#SEND TRANSACTIONS

This part is still challenging work quite well under normal loads.
too bad our proxy did not followed rythm to find out
python stress_burn_egld_4.py \
--wallets-dir ./win4-wallets/shard-0 \
--wallets-dir ./win4-wallets/shard-1 \
--wallets-dir ./win4-wallets/shard-2 \
--max-wallets 500 \
--batch-size 95 \
--gateway 90.12.225.178:8079 \
--dry-run

python stress_burn_egld.py \
--wallets-dir ./win4-wallets/shard-0 \
--max-wallets 500 \
--batch-size 95

python stress_burn_egld.py \
--wallets-dir ./win4-wallets/shard-0 \
--max-wallets 500 \
--batch-size 95

python stress_burn_egld.py \
--wallets-dir ./win4-wallets/shard-0 \
--max-wallets 500 \
--batch-size 95
