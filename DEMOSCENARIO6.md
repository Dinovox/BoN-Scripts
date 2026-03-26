# DEMO SCENARIO 6 — Contract Storm

Challenge 4 : appels de smart contract via forwarder-blind → xExchange DEX pair.
60 minutes, 4 types d'appels, minimum 300 txs réussies par type.

---

## Schedule

| Étape                       | Heure UTC        | Quoi                                               |
| --------------------------- | ---------------- | -------------------------------------------------- |
| Prep wallets + deploy       | Avant le 26 mars | Wallets générés, forwarders déployés, WEGLD wrappé |
| Réception des fonds         | 26 mars ~15:45   | 500 EGLD reçus sur le wallet guild leader          |
| Fund stress wallets         | 15:45 – 15:55    | Distribuer EGLD aux 100 spam wallets               |
| Wrap EGLD → WEGLD           | 15:55 – 16:00    | Wrap sur chaque wallet                             |
| **CHALLENGE START**         | **16:00 UTC**    | **Lancer stress_unified.py --mode contract**       |
| Auto-drain (continu)        | 16:00 – 17:00    | Drain automatique toutes les 60s (intégré)         |
| **CHALLENGE END**           | **17:00 UTC**    | Couper le script                                   |
| Drain final + récup wallets | 17:00 – 17:15    | drain_forwarders.py + drain_wallets.py             |

---

## Contraintes critiques

| Règle                                                  | Pourquoi                                                         |
| ------------------------------------------------------ | ---------------------------------------------------------------- |
| Ne pas lancer avant 16:00 UTC                          | Les calls pre-window ne comptent pas                             |
| `blindSync` uniquement depuis shard 1                  | Call synchrone : forwarder et DEX doivent être sur le même shard |
| Drain requis après tout call async/transf cross-shard  | Les tokens s'accumulent dans le forwarder sinon                  |
| `blindTransfExec` accumule toujours (même shard aussi) | Drain nécessaire même sur shard 1                                |
| Wallets déployeurs = wallets séparés des spam wallets  | Évite les conflits de nonce (owner du contrat ≠ spammer)         |

---

## Budget estimé (500 EGLD)

| Poste                                          | Calcul            | Montant   |
| ---------------------------------------------- | ----------------- | --------- |
| Gas stress calls (2 500 txs × 70M gas × 1GWei) | 2 500 × 0.07 EGLD | ~175 EGLD |
| Gas wrap (100 wallets × 1 wrap × 3M gas)       | 100 × 0.003 EGLD  | ~0.3 EGLD |
| Gas drain auto (4 shards × 60 drains × 10M)    | ~24 EGLD          | ~24 EGLD  |
| Capital de swap EGLD (0.1 WEGLD × 100 wallets) | recyclé via drain | ~10 EGLD  |
| **Total estimé**                               |                   | ~210 EGLD |

> Capital WEGLD recyclé : WEGLD → USDC via forwarder, drain USDC, re-swap USDC → WEGLD.
> La consommation réelle est quasi uniquement le gas.

---

## Adresses clés

```
DEX pair (destination forwarder) : erd1qqqqqqqqqqqqqpgqeel2kumf0r8ffyhth7pqdujjat9nx0862jpsg2pqaq
WEGLD wrap SC shard 0            : erd1qqqqqqqqqqqqqpgqvc7gdl0p4s97guh498wgz75k8sav6sjfjlwqh679jy
WEGLD wrap SC shard 1            : erd1qqqqqqqqqqqqqpgqhe8t5jewej70zupmh44jurgn29psua5l2jps3ntjj3
WEGLD wrap SC shard 2            : erd1qqqqqqqqqqqqqpgqmuk0q2saj0mgutxm4teywre6dl8wqf58xamqdrukln
WEGLD token ID                   : WEGLD-bd4d79
USDC token ID                    : USDC-c76f1f
WASM forwarder                   : ./contract/dex-interactor/forwarder-blind-bon.wasm
```

---

## 1. PRÉPARER LES WALLETS DÉPLOYEURS

> Un wallet par shard, **séparé des spam-wallets**. Ces wallets sont owners des contrats.

```bash
# Générer 1 wallet par shard (utilise generate_wallets_2.py qui cible les shards automatiquement)
python generate_wallets_2.py \
  --target-per-shard 1 \
  --output-dir ./deploy-wallets \
  --num-shards 3
```

Financer chaque deployer avec ~1 EGLD (gas de déploiement + drains futurs) :

```bash
python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./deploy-wallets/shard-0 \
  --amount 1 \
  --dry-run

python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./deploy-wallets/shard-1 \
  --amount 1 \
  --dry-run

python fund_wallets_2.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./deploy-wallets/shard-2 \
  --amount 1 \
  --dry-run
```

---

## 2. DÉPLOYER LES FORWARDER CONTRACTS

> 1 contrat par shard. À faire **bien avant le challenge** (utiliser la preparation window).
> Le WASM officiel sanctionné est dans `./contract/dex-interactor/`.
> **Important** : le flag `--metadata-payable-by-sc` est obligatoire — le DEX renvoie les tokens
> via callback SC vers le forwarder. Sans ce flag, le SC rejette les callbacks avec
> `"sending value to non payable contract"` et les txs échouent.

### Si les contrats sont déjà déployés sans `--metadata-payable-by-sc` → upgrade

```bash
# Upgrade shard-0
mxpy contract upgrade erd1qqqqqqqqqqqqqpgqcedk2c63prd9avzcf67ler6ev79clf7pz2yqm7d6a7 \
  --bytecode ./contract/dex-interactor/forwarder-blind-bon.wasm \
  --pem ./deploy-wallets/shard-0/wallet_000.pem \
  --gas-limit 100000000 \
  --proxy https://gateway.battleofnodes.com \
  --chain B \
  --metadata-payable-by-sc \
  --recall-nonce --send

# Upgrade shard-1
mxpy contract upgrade erd1qqqqqqqqqqqqqpgqp0aq3n0qzwfac6qd4gx5hyrc62c2fmtlme7s0kj679 \
  --bytecode ./contract/dex-interactor/forwarder-blind-bon.wasm \
  --pem ./deploy-wallets/shard-1/wallet_000.pem \
  --gas-limit 100000000 \
  --proxy https://gateway.battleofnodes.com \
  --chain B \
  --metadata-payable-by-sc \
  --recall-nonce --send

# Upgrade shard-2
mxpy contract upgrade erd1qqqqqqqqqqqqqpgq0z7zzhjqm2lgtvhqwxg4gtpjr9wq2j6pvmrqvhveyw \
  --bytecode ./contract/dex-interactor/forwarder-blind-bon.wasm \
  --pem ./deploy-wallets/shard-2/wallet_000.pem \
  --gas-limit 100000000 \
  --proxy https://gateway.battleofnodes.com \
  --chain B \
  --metadata-payable-by-sc \
  --recall-nonce --send
```

> Les adresses des forwarders sont dans `forwarders.json` (champ `contract`).
> L'upgrade ne change pas l'adresse du contrat.

```bash
python deploy_forwarders.py \
  --wallets-dir ./deploy-wallets \
  --wasm-path ./contract/dex-interactor/forwarder-blind-bon.wasm \
  --shards 0 1 2 \
  --gas-limit 30000000 \
  --out forwarders.json \
  --dry-run
```

> Vérifier que `forwarders.json` contient les 3 adresses de contrats après déploiement.

```bash
cat forwarders.json
# Attendu :
# {
#   "shard-0": {"deployer_pem": "...", "tx_hash": "...", "contract": "erd1..."},
#   "shard-1": {"deployer_pem": "...", "tx_hash": "...", "contract": "erd1..."},
#   "shard-2": {"deployer_pem": "...", "tx_hash": "...", "contract": "erd1..."}
# }
```

---

## 3. FUND STRESS WALLETS (15:45 UTC)

> 100 wallets spam-wallets, ~2 EGLD par wallet pour gas (70M gas × 1 GWei = 0.07 EGLD/call).
> Lancer dès réception des 500 EGLD du guild leader.

```bash

python generate_wallets_2.py \
  --num-shards 3 \
  --output-dir ./scen6-wallets \
  --total 97 \
  --dry-run

```

Vérifier les balances en EGLD :

```bash
python check_balances_3.py \
  --wallets-dir ./scen6-wallets/shard-0 \
  --wallets-dir ./scen6-wallets/shard-1 \
  --wallets-dir ./scen6-wallets/shard-2 \
  --from-wallet ./wallets/bon_supernova.pem \
  --max-wallets 97 \
  --min-egld 4.5 \
  --dry-run


  python check_balances_3.py \
  --wallets-dir ./deploy-wallets/shard-0 \
  --wallets-dir ./deploy-wallets/shard-1 \
  --wallets-dir ./deploy-wallets/shard-2 \
  --from-wallet ./wallets/bon_supernova.pem \
  --max-wallets 3 \
  --min-egld 5 \
  --dry-run
```

---

## 4. WRAP EGLD → WEGLD (15:55 UTC)

> Chaque wallet wrape exactement le déficit manquant pour atteindre 0.1 WEGLD.
> `check_balances_3.py --wrap-wegld 0.1` : 1 tx `wrapEgld` par wallet concerné, signé depuis
> son propre PEM. Pas de spam, pas de race condition sur le batch_count.

```bash
python check_balances_3.py \
  --wallets-dir ./scen6-wallets/shard-0 \
  --wallets-dir ./scen6-wallets/shard-1 \
  --wallets-dir ./scen6-wallets/shard-2 \
  --max-wallets 97 \
  --wrap-wegld 0.5 \
  --dry-run
```

Vérifier les balances WEGLD :

```bash
python check_balances_3.py \
  --wallets-dir ./scen6-wallets/shard-0 \
  --wallets-dir ./scen6-wallets/shard-1 \
  --wallets-dir ./scen6-wallets/shard-2 \
  --max-wallets 97 \
  --token WEGLD-bd4d79 --token USDC-c76f1f

python check_balances_3.py \
  --wallets-dir ./deploy-wallets/shard-0 \
  --wallets-dir ./deploy-wallets/shard-1 \
  --wallets-dir ./deploy-wallets/shard-2 \
  --max-wallets 3 \
  --token WEGLD-bd4d79 --token USDC-c76f1f


```

---

## 5. DRY-RUN (vérification pré-challenge)

> Vérifie que tout est bien configuré sans envoyer de transactions.

```bash
python stress_unified.py \
  --wallets-dir ./scen6-wallets/shard-0 \
  --wallets-dir ./scen6-wallets/shard-1 \
  --wallets-dir ./scen6-wallets/shard-2 \
  --mode contract \
  --forwarders forwarders.json \
  --call-type cycle \
  --max-wallets 97 \
  --dry-run
```

> Vérifier dans l'output que :
>
> - Les wallets shard-1 sont bien assignés à `blindSync` (25%), `blindAsyncV1`, `blindAsyncV2`, `blindTransfExec`
> - Les wallets shard-0/2 sont assignés aux 3 types async
> - Aucun wallet n'est ignoré faute de forwarder

---

## 6. CHALLENGE WINDOW — 16:00 UTC

> **Ne pas lancer avant 16:00 UTC.**
> Mode cycle : chaque wallet tourne sur tous les call types compatibles avec son shard.
> Drain réactif intégré : quand WEGLD < amount_in ET USDC < amount_in_alt, le wallet drain
> ses propres fonds bloqués dans le forwarder, puis repart dans le sens USDC→WEGLD.

```bash


# 10000000000000000 = 0.01 EGLD
# 50000 = 0.05 USDC

# 10000000000000 = 0.00001 EGLD
# 50 = 0.000050 USDC

91 973 000
45 303 469
45 986 500
3 000 000 000
44 501 500

{"lastBlock":30393102,"fast":45064563,"faster":17859623}
python stress_unified.py \
  --wallets-dir ./scen6-wallets/shard-0 \
  --wallets-dir ./scen6-wallets/shard-1 \
  --wallets-dir ./scen6-wallets/shard-2 \
  --mode contract \
  --forwarders forwarders.json \
  --call-type cycle \
  --token-in WEGLD-bd4d79 \
  --token-out USDC-c76f1f \
  --amount-in 10000000000000 \
  --max-wallets 97 \
  --alternate-swap --amount-in-alt 50 \
  --gaswar --gaswar-strategy auto --gaswar-max 10000000000 \
  --gaswar-min 2000000000 \
  --batch-size 95 \
  --drain-interval 60 \
  --dry-run
```

> **Objectif milestone bonus** : 2 500 txs réussies au plus vite.
> Suivi live sur `bon.multiversx.com/guild-wars` (refresh ~30s).

### Résumé du routing par shard

| Shard | Types assignés (mode cycle)                            | Forwarder utilisé |
| ----- | ------------------------------------------------------ | ----------------- |
| 0     | blindAsyncV1, blindAsyncV2, blindTransfExec            | shard-0 forwarder |
| 1     | blindSync, blindAsyncV1, blindAsyncV2, blindTransfExec | shard-1 forwarder |
| 2     | blindAsyncV1, blindAsyncV2, blindTransfExec            | shard-2 forwarder |

### Output attendu

```
[INFO] 200 txs  18 tx/s  [blindSync:52  blindAsyncV1:51  blindAsyncV2:49  blindTransfExec:48]
[DRAIN] shard-0: 2/2 drain txs envoyées
[DRAIN] shard-1: 2/2 drain txs envoyées
[DRAIN] shard-2: 2/2 drain txs envoyées
...
[TARGET ATTEINT] 2500 txs en 142.0s (17 tx/s)
  ✓ blindSync:626
  ✓ blindAsyncV1:625
  ✓ blindAsyncV2:625
  ✓ blindTransfExec:624
```

---

## 7. DRAIN FINAL DES FORWARDERS (17:00 UTC)

> Après la fin du challenge, drainer les tokens restants dans les forwarders.
> Les deployer wallets (owners) envoient les txs de drain.

```bash
python drain_forwarders.py \
  --forwarders forwarders.json \
  --wallets-dir ./deploy-wallets \
  --shards 0 1 2 \
  --dry-run
```

---

## 8. DRAIN DES STRESS WALLETS

> Récupérer le solde EGLD + ESDT restant sur les spam-wallets.
> **Changer `--to` !**

```bash
python drain_wallets.py \
  --wallets-dir ./spam-wallets/shard-0 \
  --wallets-dir ./spam-wallets/shard-1 \
  --wallets-dir ./spam-wallets/shard-2 \
  --to erd1f60kcmly42f6l9v90l9f0s0rq5dja8lvcuuu7hm3hr3skyae0hgq0mfmnl \
  --dry-run
```

---

## 9. CLEANUP POST-CHALLENGE

> Ordre à respecter : drain SC → swap USDC→WEGLD → drain wallets EGLD.

### 9.1 Drain des forwarder SCs (WEGLD + USDC)

> Envoie `drain@<token_hex>@00` depuis chaque wallet déployeur (owner).
> Les tokens atterrissent sur les deploy-wallets.

```bash
python drain_forwarders.py \
  --forwarders forwarders.json \
  --shards 0 1 2 \
  --dry-run
```

### 9.2 Swap USDC → WEGLD (tous les wallets, direct DEX)

> Pour chaque wallet ayant du USDC (scen6 + deployers), envoie 1 ESDTTransfer
> direct vers le DEX pair (pas via forwarder). Swap toute la balance USDC d'un coup.

```bash
# Dry-run : affiche les swaps sans envoyer
python swap_to_wegld.py \
  --wallets-dir ./scen6-wallets/shard-0 \
  --wallets-dir ./scen6-wallets/shard-1 \
  --wallets-dir ./scen6-wallets/shard-2 \
  --wallets-dir ./deploy-wallets/shard-0 \
  --wallets-dir ./deploy-wallets/shard-1 \
  --wallets-dir ./deploy-wallets/shard-2 \
  --dry-run

# Live
python swap_to_wegld.py \
  --wallets-dir ./scen6-wallets/shard-0 \
  --wallets-dir ./scen6-wallets/shard-1 \
  --wallets-dir ./scen6-wallets/shard-2 \
  --wallets-dir ./deploy-wallets/shard-0 \
  --wallets-dir ./deploy-wallets/shard-1 \
  --wallets-dir ./deploy-wallets/shard-2
```

### 9.3 Unwrap WEGLD → EGLD (tous les wallets)

> Une fois les USDC swappés en WEGLD, unwrapper les WEGLD pour récupérer de l'EGLD pur.
> Chaque wallet envoie son solde WEGLD complet vers le wrap SC de son shard.

```bash
python unwrap_wegld.py \
  --wallets-dir ./scen6-wallets/shard-0 \
  --wallets-dir ./scen6-wallets/shard-1 \
  --wallets-dir ./scen6-wallets/shard-2 \
  --wallets-dir ./deploy-wallets/shard-0 \
  --wallets-dir ./deploy-wallets/shard-1 \
  --wallets-dir ./deploy-wallets/shard-2 \
  --dry-run
```

### 9.4 Drain EGLD des wallets → wallet principal

> Après que les unwraps soient confirmés, rapatrier l'EGLD.
> **Changer `--to` !**
> **Changer `--to` !**

```bash
# Scen6 wallets
python drain_wallets.py \
  --wallets-dir ./scen6-wallets/shard-0 \
  --wallets-dir ./scen6-wallets/shard-1 \
  --wallets-dir ./scen6-wallets/shard-2 \
  --to erd1f60kcmly42f6l9v90l9f0s0rq5dja8lvcuuu7hm3hr3skyae0hgq0mfmnl \
  --dry-run

# Deploy wallets
python drain_wallets.py \
  --wallets-dir ./deploy-wallets/shard-0 \
  --wallets-dir ./deploy-wallets/shard-1 \
  --wallets-dir ./deploy-wallets/shard-2 \
  --to erd1f60kcmly42f6l9v90l9f0s0rq5dja8lvcuuu7hm3hr3skyae0hgq0mfmnl \
  --dry-run
```

---

## Troubleshooting

| Symptôme                                      | Cause probable                    | Fix                                                                  |
| --------------------------------------------- | --------------------------------- | -------------------------------------------------------------------- |
| Wallet ignoré "pas de forwarder pour shard X" | forwarders.json manque un shard   | Redéployer pour le shard manquant                                    |
| Wallet ignoré "blindSync requiert shard 1"    | Normal pour shard 0 et 2          | Utiliser `cycle` ou un autre call-type                               |
| `[WARN] 0/90 txs accepted`                    | Balance EGLD insuffisante         | Refund wallets (check_balances_3.py)                                 |
| Tokens bloqués dans forwarder après challenge | drain_interval trop long ou coupé | Relancer drain_forwarders.py manuellement                            |
| Score faible sur un type d'appel              | Pas assez de shard-1 wallets      | Augmenter wallets shard-1 ou passer à `--call-type blindSync` séparé |
