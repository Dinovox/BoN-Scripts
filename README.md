> [!WARNING]
> **THIS SCRIPT WAS PRODUCED AS PART OF THE BATTLE OF NODES — GUILD WARS, UNDER TIGHT TIME CONSTRAINTS.**
> It contains **known bugs that have not been fixed** and **unknown bugs that have been fixed even less**.
> **Do not use in production without proper review and correction.**

# BoN On-Chain Scripts

Scripts Python pour le **Battle of Nodes (BoN)** — réseau de test MultiversX.
Couvre la génération de wallets, le financement, les stress tests et les interactions governance/DEX.

---

## Prérequis

- Python **3.10+**
- `pip`

---

## Installation

```bash
# Option A — script automatique
bash setup.sh

# Option B — manuel
python3 -m venv venv
source venv/bin/activate          # macOS / Linux
# .\venv\Scripts\activate         # Windows

pip install --upgrade multiversx-sdk
```

> Toutes les commandes ci-dessous supposent que le venv est **activé**.

---

## Configuration

[config.py](config.py) contient les constantes réseau :

| Constante           | Valeur                               |
| ------------------- | ------------------------------------ |
| `GATEWAY_URL`       | `https://gateway.battleofnodes.com/` |
| `CHAIN_ID`          | `B`                                  |
| `DEFAULT_GAS_PRICE` | `1 000 000 000` (1 GWei)             |
| `GOVERNANCE_SC`     | `erd1qqq...ylll`                     |
| `STAKING_SC`        | `erd1qqq...czs7`                     |

Modifier `GATEWAY_URL` si le réseau change d'endpoint. Tous les scripts acceptent aussi `--gateway <url>` pour surcharger sans toucher au fichier.

---

## Workflow Challenge — Stress Test #4

### Structure des wallets

```
wallets/
  bon_supernova.pem          ← wallet principal (shard 1) — ne pas mettre dans shard-X/
  relayer0.pem               ← relayer shard-0 (pour stress_cross_shard_relayed.py)
  relayer1.pem               ← relayer shard-1
  relayer2.pem               ← relayer shard-2
  shard-0/  wallet_000.pem  wallet_001.pem  …
  shard-1/  wallet_000.pem  …               ← même shard que bon_supernova
  shard-2/  wallet_000.pem  …
```

> `bon_supernova.pem` et les relayers restent à la racine de `wallets/` pour ne pas être confondus avec les wallets de challenge.

---

### Étape 1 — Générer les wallets de challenge

Génère des wallets ed25519 et les trie automatiquement par shard dans des sous-dossiers.

```bash
# S'arrêter quand chaque shard a 100 wallets (~300 générés au total)
python generate_wallets.py --target-per-shard 100 --output-dir ./wallets

# Aperçu sans écrire de fichiers
python generate_wallets.py --count 20 --dry-run
```

---

### Étape 1b — Générer les wallets relayers (pour RelayedV3)

Nécessaire uniquement pour `stress_cross_shard_relayed.py`.
Génère 1 wallet par shard, placé dans le bon shard automatiquement.

```bash
# Génère 1 wallet par shard dans un dossier temporaire
python generate_wallets.py \
  --target-per-shard 1 \
  --output-dir ./wallets/tmp_relayers

# Déplace et renomme
mv ./wallets/tmp_relayers/shard-0/wallet_000.pem ./wallets/relayer0.pem
mv ./wallets/tmp_relayers/shard-1/wallet_000.pem ./wallets/relayer1.pem
mv ./wallets/tmp_relayers/shard-2/wallet_000.pem ./wallets/relayer2.pem
rmdir ./wallets/tmp_relayers/shard-{0,1,2} ./wallets/tmp_relayers

# Financer les relayers depuis bon_supernova (~2 EGLD chacun pour 10k txs)
python send_tx.py --wallet ./wallets/bon_supernova.pem --to erd1avtfvajwhk3jk3d8a2c8fs65jya0ss2jap503f6l0c26s7sgrs7qduqf3m --value 2.0
python send_tx.py --wallet ./wallets/bon_supernova.pem --to erd189c2pc8gskk6melsjp4zy623cj776c5ekgcuu7rwul0ez3hqcy0scmhx8v --value 2.0
python send_tx.py --wallet ./wallets/bon_supernova.pem --to erd16c55f9d5k7tf2zzk7hwt6lv0acdt2vjfw2zc0nxf7wj6x0t9g7aqk8fgvq --value 2.0
```

> Les adresses bech32 des relayers sont dans le header de chaque fichier PEM généré.

---

### Étape 2 — Financer les wallets de challenge

Envoie de l'EGLD depuis `bon_supernova.pem` vers les wallets shard-1 (même shard = tx directe).

**Estimation du montant nécessaire par wallet :**

- Coût gas d'une MoveBalance : `50 000 × 1 GWei = 0.00005 EGLD`
- Pour 500 txs par wallet : `500 × 0.00005 = 0.025 EGLD`
- Recommandé avec marge : **0.05 EGLD par wallet**

```bash
python fund_wallets.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./wallets/shard-0 \
  --amount 0.05 \
  --max_wallet 30

# Dry-run pour vérifier avant d'envoyer
python fund_wallets.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./wallets/shard-1 \
  --amount 0.05 --dry-run
```

---

### Étape 2b — Wrapper l'EGLD en WEGLD (avant Window B)

Nécessaire pour alimenter les wallets en WEGLD avant les DEX swaps.

```bash
# Dry-run
python wrap_egld.py --wallet ./wallets/bon_supernova.pem --amount 10.0 --dry-run

# Production (adapter le montant : 100 wallets × 0.05 = 5 EGLD minimum)
python wrap_egld.py --wallet ./wallets/bon_supernova.pem --amount 10.0
```

---

### Étape 2c — Distribuer le WEGLD aux wallets de challenge

```bash
# Dry-run
python fund_esdt.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./wallets/shard-0 \
  --token-id WEGLD-bd4d79 \
  --max-wallets 33 \
  --amount 0.05 --dry-run

# Production
python fund_esdt.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./wallets/shard-1 \
  --token-id WEGLD-bd4d79 \
  --amount 0.05
```

---

### Étape 3 — Window A : 50 000 MoveBalance (14h00–15h00 UTC)

Envoie 50 000 transferts EGLD intra-shard en parallèle (un thread par wallet).
`--exclude-wallet` protège `bon_supernova.pem` s'il se retrouve dans le dossier.

```bash
# Dry-run d'abord
python stress_move_balance.py \
  --wallets-dir ./wallets/shard-1 \
  --exclude-wallet ./wallets/bon_supernova.pem \
  --value 0.0001 \
  --target 500 --dry-run

# Production
python stress_move_balance.py \
  --wallets-dir ./wallets/shard-2 \
  --exclude-wallet ./wallets/bon_supernova.pem \
  --value 0.0001 \
  --target 10000 \
  --max-wallets 33
```

---

### Étape 4 — Window B : 1 000 DEX Swaps (15h00–17h00 UTC)

Appels `swapTokensFixedInput` sur le pool DEX BoN.

> Vérifier les **token IDs exacts** (ex. `WEGLD-xxxxxx`) depuis l'explorer BoN avant de lancer.

```bash
# Pool : erd1qqqqqqqqqqqqqpgqeel2kumf0r8ffyhth7pqdujjat9nx0862jpsg2pqaq

# Dry-run   --wallet ./wallets/shard-0/wallet_000.pem \

python stress_dex_swap.py \
  --wallets-dir ./wallets/shard-0 \
  --token-in  WEGLD-bd4d79 \
  --amount-in 0.001 \
  --token-out USDC-c76f1f \
  --min-out 1 \
  --max-wallets 33 \
  --target 200 --dry-run

# Production — plusieurs wallets pour accélérer
python stress_dex_swap.py \
  --wallet ./wallets/shard-1/wallet_000.pem \
  --wallet ./wallets/shard-1/wallet_001.pem \
  --token-in  WEGLD-bd4d79 \
  --amount-in 0.001 \
  --token-out USDC-c76f1f \
  --min-ou 1 \
  --max-wallets 33 \
  --target 1100
```

---

## Référence des scripts

### Vue d'ensemble

| Script                          | Rôle                                                 |
| ------------------------------- | ---------------------------------------------------- |
| `generate_wallets.py`           | Génère des wallets PEM triés par shard               |
| `fund_wallets.py`               | Finance N wallets depuis un wallet principal         |
| `wrap_egld.py`                  | Wrapper EGLD → WEGLD via le SC de wrapping           |
| `fund_esdt.py`                  | Distribue des tokens ESDT vers N wallets             |
| `check_balances.py`             | Affiche les balances EGLD + ESDT de tous les wallets |
| `stress_move_balance.py`        | Window A — 50k transferts intra-shard                |
| `stress_cross_shard.py`         | 50k transferts cross-shard (0→1→2→0)                 |
| `stress_cross_shard_relayed.py` | 10k transferts cross-shard via RelayedV3             |
| `stress_dex_swap.py`            | Window B — 1k swaps DEX                              |
| `stress_dex_swap_relayed.py`    | 500 swaps DEX via RelayedV3                          |
| `send_tx.py`                    | Envoi de transaction générique                       |
| `governance_vote.py`            | Vote sur une proposition governance                  |
| `query_sc.py`                   | Lecture (vmQuery) d'un smart contract                |

---

### `generate_wallets.py`

```
--count N               Nombre total de wallets à générer
--target-per-shard N    S'arrête quand chaque shard a N wallets (exclusif avec --count)
--output-dir DIR        Dossier de sortie (défaut: ./wallets)
--num-shards N          Nombre de shards (défaut: 3)
--dry-run               Afficher sans écrire
```

---

### `fund_wallets.py`

```
--from-wallet PATH      Wallet de financement PEM (requis)
--wallets-dir PATH      Dossier contenant les wallets à financer (requis)
--amount FLOAT          EGLD par wallet (défaut: 0.1)
--max-wallets N         Limite de wallets à financer (défaut: 100)
--dry-run               Afficher sans envoyer
--gateway URL           URL du gateway (défaut: config.GATEWAY_URL)
```

---

### `wrap_egld.py`

Envoie de l'EGLD au smart contract de wrapping (`wrapEgld`) pour obtenir du WEGLD.
Le sender doit être sur le même shard que le wrap SC (shard 1).

```bash
# Dry-run
python wrap_egld.py --wallet ./wallets/bon_supernova.pem --amount 10.0 --dry-run

# Production
python wrap_egld.py --wallet ./wallets/bon_supernova.pem --amount 10.0
```

```
--wallet PATH       Wallet PEM (défaut: ./wallets/bon_supernova.pem)
--amount FLOAT      EGLD à wrapper (requis)
--wrap-sc BECH32    Contrat de wrapping (défaut: config.WEGLD_WRAP_SC_SHARD1)
--gas N             Gas limit (défaut: 3 000 000)
--dry-run
--gateway URL
```

---

### `fund_esdt.py`

Distribue des tokens ESDT depuis un wallet principal vers chaque `.pem` d'un dossier
(identique à `fund_wallets.py` mais utilise `ESDTTransfer`).

```bash
# Dry-run
python fund_esdt.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./wallets/shard-1 \
  --token-id WEGLD-bd4d79 \
  --amount 0.05 --dry-run

# Production
python fund_esdt.py \
  --from-wallet ./wallets/bon_supernova.pem \
  --wallets-dir ./wallets/shard-1 \
  --token-id WEGLD-bd4d79 \
  --amount 0.05
```

```
--from-wallet PATH    Wallet expéditeur PEM (requis)
--wallets-dir PATH    Dossier contenant les .pem destinataires (requis)
--token-id STRING     Identifiant du token (ex: WEGLD-bd4d79)
--amount FLOAT        Montant par wallet (unités humaines, requis)
--decimals N          Décimales du token (défaut: 18)
--max-wallets N       Limite de wallets à financer (défaut: 100)
--gas N               Gas limit par tx (défaut: 500 000)
--dry-run
--gateway URL
```

---

### `stress_move_balance.py`

```
--wallets-dir PATH      Dossier avec les .pem (requis)
--target N              Nombre total de transactions (défaut: 50000)
--shard N               Forcer un shard spécifique (auto si omis)
--num-shards N          Nombre de shards (défaut: 3)
--value FLOAT           EGLD par tx (défaut: 0.0)
--max-wallets N         Max wallets à utiliser (défaut: 100)
--delay FLOAT           Délai entre txs par wallet en secondes (défaut: 0)
--exclude-wallet PATH   Exclure un wallet PEM du pool (ex: wallet principal)
--dry-run               Construire/signer sans envoyer
--gateway URL
```

---

### `stress_cross_shard.py`

50 000 transferts cross-shard en parallèle. Chaque wallet envoie vers le shard suivant :
`shard-0 → shard-1 → shard-2 → shard-0`.

```bash
# Dry-run
python stress_cross_shard.py \
  --shard-dir-0 ./wallets/shard-0 \
  --shard-dir-1 ./wallets/shard-1 \
  --shard-dir-2 ./wallets/shard-2 \
  --max-wallets 30 \
  --value 0.0001 \
  --target 10000 --dry-run

# Production
python stress_cross_shard.py \
  --shard-dir-0 ./wallets/shard-0 \
  --shard-dir-1 ./wallets/shard-1 \
  --shard-dir-2 ./wallets/shard-2 \
  --max-wallets 30 \
  --value 0.0001 \
  --target 1000
```

```
--shard-dir-0 PATH    Dossier wallets shard-0 (requis)
--shard-dir-1 PATH    Dossier wallets shard-1 (requis)
--shard-dir-2 PATH    Dossier wallets shard-2 (requis)
--max-wallets N       Max wallets par shard (défaut: 100)
--target N            Total transactions (défaut: 50000)
--value FLOAT         EGLD par tx (défaut: 0.0)
--delay FLOAT         Délai entre txs par wallet en secondes (défaut: 0)
--dry-run
--gateway URL
```

---

### `stress_cross_shard_relayed.py`

10 000 transactions cross-shard via **RelayedV1** : chaque wallet signe une inner tx,
un relayer dédié par shard l'encapsule et paie le gas.

Nécessite 3 wallets relayers pré-financés (un par shard), distincts des wallets de challenge.

```bash
# Dry-run
python stress_cross_shard_relayed.py \
  --shard-dir-0 ./wallets/shard-0 \
  --shard-dir-1 ./wallets/shard-1 \
  --shard-dir-2 ./wallets/shard-2 \
  --relayer-0 ./wallets/relayer0.pem \
  --relayer-1 ./wallets/relayer1.pem \
  --relayer-2 ./wallets/relayer2.pem \
  --value 0.0001 \
  --max-wallets 30 --target 100 --dry-run

# Production
python stress_cross_shard_relayed.py \
  --shard-dir-0 ./wallets/shard-0 \
  --shard-dir-1 ./wallets/shard-1 \
  --shard-dir-2 ./wallets/shard-2 \
  --relayer-0 ./wallets/relayer0.pem \
  --relayer-1 ./wallets/relayer1.pem \
  --relayer-2 ./wallets/relayer2.pem \
  --value 0.0001 \
  --max-wallets 30 --target 10000
```

```
--shard-dir-0 PATH    Dossier wallets shard-0 (requis)
--shard-dir-1 PATH    Dossier wallets shard-1 (requis)
--shard-dir-2 PATH    Dossier wallets shard-2 (requis)
--relayer-0 PATH      Wallet relayer shard-0 PEM (requis)
--relayer-1 PATH      Wallet relayer shard-1 PEM (requis)
--relayer-2 PATH      Wallet relayer shard-2 PEM (requis)
--max-wallets N       Max wallets par shard (défaut: 100)
--target N            Total transactions (défaut: 10000)
--value FLOAT         EGLD par inner tx (défaut: 0.0)
--dry-run
--gateway URL
```

> Gas outer tx : 100 000 (50k inner + 50k overhead RelayedV1) — payé par le relayer.
> Prévoir ~10 000 × 100 000 × 1 GWei = **1 EGLD par relayer** pour 10k txs.

---

### `stress_dex_swap.py`

```
--wallet PATH           Wallet PEM (requis, répétable pour plusieurs wallets)
--token-in TOKEN_ID     Token d'entrée (ex: WEGLD-bd4d79)
--amount-in FLOAT       Montant par swap (unités humaines)
--token-out TOKEN_ID    Token de sortie
--min-out N             Montant minimum de sortie en raw (défaut: 1)
--decimals N            Décimales du token d'entrée (défaut: 18)
--target N              Nombre de swaps (défaut: 1000)
--gas N                 Gas limit par swap (défaut: 12 000 000)
--dry-run
--gateway URL
```

---

### `stress_dex_swap_relayed.py`

500 swaps DEX via **RelayedV3**, depuis 1, 2 ou 3 shards simultanément.
Le relayer de chaque shard paie le gas. Tous les wallets ciblent le même pool DEX (shard 1).

- **Shard-1 wallets → DEX** = intra-shard
- **Shard-0/2 wallets → DEX** = cross-shard

```bash
# Tous les shards (intra + cross simultanément)
python stress_dex_swap_relayed.py \
  --shard-dir-0 ./wallets/shard-0 --relayer-0 ./wallets/relayer0.pem \
  --shard-dir-1 ./wallets/shard-1 --relayer-1 ./wallets/relayer1.pem \
  --shard-dir-2 ./wallets/shard-2 --relayer-2 ./wallets/relayer2.pem \
  --token-in WEGLD-bd4d79 --amount-in 0.001 --token-out USDC-c76f1f \
  --min-out 1 \
  --max-wallets 33 --target 100 --dry-run

# Un seul shard (ex: shard-1 intra-shard uniquement)
python stress_dex_swap_relayed.py \
  --shard-dir-1 ./wallets/shard-1 --relayer-1 ./wallets/relayer1.pem \
  --token-in WEGLD-bd4d79 --amount-in 0.001 --token-out USDC-c76f1f \
  --min-out 1 \
  --max-wallets 30 --target 100 \
  --dry-run



```

```
--shard-dir-0 PATH    Dossier wallets shard-0 (optionnel, avec --relayer-0)
--relayer-0 PATH      Relayer shard-0 PEM
--shard-dir-1 PATH    Dossier wallets shard-1 (optionnel, avec --relayer-1)
--relayer-1 PATH      Relayer shard-1 PEM
--shard-dir-2 PATH    Dossier wallets shard-2 (optionnel, avec --relayer-2)
--relayer-2 PATH      Relayer shard-2 PEM
--max-wallets N       Max wallets par shard (défaut: 100)
--token-in TOKEN_ID   Token d'entrée (requis)
--amount-in FLOAT     Montant par swap (unités humaines, requis)
--token-out TOKEN_ID  Token de sortie (requis)
--min-out N           Montant minimum de sortie en raw (défaut: 1)
--decimals N          Décimales du token d'entrée (défaut: 18)
--target N            Nombre de swaps (défaut: 500)
--gas N               Gas limit par swap (défaut: 12 050 000)
--dry-run
--gateway URL
```

> Au moins un couple `--shard-dir-X` + `--relayer-X` requis.
> Gas estimé : 500 × 12 050 000 × 1 GWei ≈ **6 EGLD** réparti entre les relayers actifs.

---

### `check_balances.py`

Affiche les balances EGLD, WEGLD et USDC (ou tout autre token ESDT) pour tous les wallets
d'un ou plusieurs dossiers, plus les wallets individuels (relayers).

```bash
# Tous les shards + relayers (affichage seul)
python check_balances.py \
  --wallets-dir ./wallets/shard-0 \
  --wallets-dir ./wallets/shard-1 \
  --wallets-dir ./wallets/shard-2 \
  --relayer ./wallets/relayer0.pem \
  --relayer ./wallets/relayer1.pem \
  --relayer ./wallets/relayer2.pem \
  --max-wallets 30

# Top-up automatique — dry-run d'abord
python check_balances.py \
  --wallets-dir ./wallets/shard-0 \
  --wallets-dir ./wallets/shard-1 \
  --wallets-dir ./wallets/shard-2 \
  --relayer ./wallets/relayer0.pem \
  --relayer ./wallets/relayer1.pem \
  --relayer ./wallets/relayer2.pem \
  --from-wallet ./wallets/bon_supernova.pem \
  --min-egld 0.1 \
  --min-esdt WEGLD-bd4d79 0.05 \
  --max-wallets 30 --dry-run

# Top-up production
python check_balances.py \
  --wallets-dir ./wallets/shard-0 \
  --wallets-dir ./wallets/shard-1 \
  --wallets-dir ./wallets/shard-2 \
  --relayer ./wallets/relayer0.pem \
  --relayer ./wallets/relayer1.pem \
  --relayer ./wallets/relayer2.pem \
  --from-wallet ./wallets/bon_supernova.pem \
  --min-egld 0.1 \
  --min-esdt WEGLD-bd4d79 0.05 \
  --max-wallets 30
```

```
--wallets-dir PATH          Dossier contenant des .pem (répétable, récursif, optionnel)
--relayer PATH              Fichier PEM individuel à inclure (répétable, optionnel)
--token TOKEN_ID            Token ESDT à afficher (répétable, défaut: WEGLD-bd4d79 USDC-c76f1f)
--max-wallets N             Limite de wallets par dossier (0 = tous, défaut: 0)
--from-wallet PATH          Wallet source pour les top-ups (requis si --min-egld ou --min-esdt)
--min-egld FLOAT            Seuil EGLD : envoie le déficit exact aux wallets en dessous
--min-esdt TOKEN_ID FLOAT   Seuil ESDT (répétable) : ex. --min-esdt WEGLD-bd4d79 0.05
--dry-run                   Afficher les top-ups sans envoyer
--gateway URL
```

> Au moins `--wallets-dir` ou `--relayer` requis.
> USDC est affiché avec 6 décimales, EGLD/WEGLD avec 18.
> Le top-up envoie uniquement le **déficit** (seuil − balance actuelle), pas un montant fixe.

---

### `send_tx.py`

Envoi de transaction générique — utile pour tester ou appeler un SC manuellement.

```bash
# Transfert EGLD
python send_tx.py --wallet ./wallet.pem --to erd1abc... --value 0.5

# Appel SC
python send_tx.py --wallet ./wallet.pem --to erd1qqq... \
  --data "claimRewards" --gas 10000000

# Dry-run avec nonce forcé
python send_tx.py --wallet ./wallet.pem --to erd1abc... \
  --data "vote@04@00" --gas 6000000 --nonce 42 --dry-run
```

```
--wallet PATH    Wallet PEM (requis)
--to BECH32      Adresse destinataire (requis)
--data STRING    Data field en clair (défaut: "")
--value FLOAT    EGLD (défaut: 0.0)
--gas N          Gas limit (défaut: 50 000)
--nonce N        Forcer le nonce (optionnel)
--dry-run
--gateway URL
```

---

### `governance_vote.py`

```bash
python governance_vote.py --wallet ./wallet.pem --proposal 4 --vote yes
python governance_vote.py --wallet ./wallet.pem --proposal 4 --vote yes --dry-run
```

```
--wallet PATH    Wallet PEM (requis)
--proposal N     Nonce de la proposition (défaut: 4)
--vote STRING    yes / no / veto / abstain (défaut: yes)
--sc BECH32      Adresse du SC governance (défaut: config.GOVERNANCE_SC)
--gas N          Gas limit (défaut: 6 000 000)
--nonce N        Forcer le nonce (optionnel)
--dry-run
--gateway URL
```

---

### `query_sc.py`

Lecture read-only (vmQuery) sur n'importe quel smart contract.

```bash
python query_sc.py --contract erd1qqq...ylll --function getProposal --args 04
python query_sc.py --contract erd1qqq...ylll --function getContractConfig
```

```
--contract BECH32    Adresse du SC (requis)
--function NAME      Fonction view (requis)
--args HEX           Arguments hex (répétable, optionnel)
--caller BECH32      Adresse de l'appelant (optionnel)
--json               Sortie JSON brute
--gateway URL
```
