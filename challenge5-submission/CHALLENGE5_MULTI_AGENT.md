# Challenge 5 — Multi-Agent Setup (10 Agents)

## Overview

Instead of running 1 agent with 1 wallet, deploy **10 independent agents**, each with:
- **Own wallet** (separate private key, nonce)
- **Own bulks** (95 txs per bulk, per agent)
- **Own registration** (MX-8004 nonce)
- **Shared command listener** (all 10 hear the same admin wallet)

**Result**: 1 GREEN command → 10 agents × 95 txs = **950 txs per command**

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│         Challenge5Orchestrator (main process)           │
│  - Spawns 10 child processes (1 per wallet)             │
│  - Monitors all agents                                  │
│  - Aggregates metrics                                   │
│  - Graceful shutdown (SIGINT)                           │
└─────────────────────────────────────────────────────────┘
                          ↓
        ┌─────────────────┼─────────────────┐
        ↓                 ↓                 ↓
    ┌────────┐      ┌────────┐      ┌────────┐
    │Agent 0 │      │Agent 1 │  …   │Agent 9 │
    │Wallet0 │      │Wallet1 │      │Wallet9 │
    └────────┘      └────────┘      └────────┘
        ↓                 ↓                 ↓
   [Listener] ← All 10 share same admin/target wallets
   [Interpreter]
   [Bulk Sender] ← Each has own nonce chain
```

---

## Timeline (March 27, 2026)

| Time (UTC) | Action | Who | Command |
|---|---|---|---|
| 15:00 | Receive 500 EGLD + wallet addresses | Guild leader | - |
| 15:05 | Generate 10 wallets | You | `npm run challenge5:gen-wallets -- --count 10` |
| 15:10 | Register 10 wallets | You | `npm run challenge5:register-wallets -- --count 10` |
| 15:20 | Fund 10 wallets (50 EGLD each) | You | Manual transfers |
| 15:45 | **Registration deadline** | System | All 10 agents must be registered |
| 16:00 | **Challenge starts** | System | Round 1 begins |
| 16:00 | Launch orchestrator | You | `npm run challenge5:multi -- --agents 10` |
| 16:30 | Round 1 ends | System | - |
| 17:00 | Round 2 starts | System | - |
| 17:30 | Challenge closes | System | Final scores |

---

## Step-by-Step Setup

### Step 1: Generate 10 Wallets (15:05 UTC)

```bash
npm run challenge5:gen-wallets -- --count 10 --output ./wallets
```

**Output**:
- `wallets/agent_00.pem` through `wallets/agent_09.pem`
- `wallets/wallets.json` (summary with addresses)

**What to do**:
- Note down all 10 addresses
- Keep wallet files secure (don't commit, don't share)

---

### Step 2: Register 10 Wallets (15:10 UTC)

```bash
npm run challenge5:register-wallets -- --wallets-dir ./wallets --count 10
```

**What happens**:
- Each wallet calls MX-8004 identity registry
- Gets assigned a unique agent nonce
- Status saved to `wallets/registration.json`

**Verify**:
- Open explorer: https://explorer.battleofnodes.com
- Search each wallet address
- Confirm MX-8004 registration txs finalized

---

### Step 3: Fund 10 Wallets (15:20 UTC)

You receive 500 EGLD on the guild leader wallet at 15:00 UTC.

**Distribute 50 EGLD to each agent wallet**:

```bash
# Using MultiversX SDK (if available):
for addr in $(cat wallets/wallets.json | jq -r '.wallets[].address'); do
  # Send 50 EGLD from guild leader to $addr
  mx-cli tx send \
    --from guild_leader.pem \
    --to $addr \
    --amount 50
done
```

Or manually via UI:
1. Copy each address from `wallets/wallets.json`
2. Use web wallet to send 50 EGLD to each
3. Verify balances: `curl https://api.battleofnodes.com/accounts/{address}`

**Total funding**: 10 × 50 = 500 EGLD ✅

---

### Step 4: Launch Orchestrator (16:00 UTC)

Once admin announces TARGET and ADMIN wallet addresses (at 15:00 UTC):

```bash
ADMIN_WALLET_ADDRESS="erd1..." \
TARGET_WALLET_ADDRESS="erd1..." \
npm run challenge5:multi -- --agents 10
```

**What happens**:
- Orchestrator spawns 10 child processes
- Each agent loads its own wallet
- All listen to the same admin wallet
- Staggered start (agent 0 at 0ms, agent 1 at 100ms, ... agent 9 at 900ms)
- Prevents nonce collision race conditions

**Output**:
```
🤖 Challenge 5 — Multi-Agent Orchestrator
════════════════════════════════════════════
📊 Configuration:
   Agents: 10
   Wallets dir: ./wallets
   Admin wallet: erd1abc...
   Target wallet: erd1def...

📂 Loading wallets from ./wallets…
   00: erd1wallet0...
   01: erd1wallet1...
   ...
   09: erd1wallet9...

✅ All 10 agents launched

📊 Agents: 10/10 running | Total score: 0 | Time: 16:00:05
```

**Each agent logs independently**:
- `logs/agent_00.log` through `logs/agent_09.log`
- `logs/orchestrator-2026-03-27.log` (central coordinator)

---

## Monitoring

### Live Dashboard

Open the web dashboard (token required):
```
http://127.0.0.1:18789/
```

Token: (check `~/.openclaw/openclaw.json`)

**View**:
- 10 agent processes running
- CPU/memory per agent
- Network I/O aggregated

### Live Scores

Every 30 seconds, check the official leaderboard:
```
https://bon.multiversx.com/guild-wars
```

**You should see**:
- Guild score = sum of all 10 agent scores
- Individual leaderboard (each agent ranked separately)

### Log Analysis

Post-challenge, inspect agent logs:

```bash
# Agent 0 logs
tail -100 logs/agent_00.log

# All agents at once
for i in {0..9}; do
  echo "=== Agent $i ==="
  tail -20 logs/agent_$(printf "%02d" $i).log
done

# Count total txs sent by all agents
grep "TX_SENT" logs/agent_*.log | wc -l

# Parse JSON logs (if you need metrics)
jq '.type' logs/agent_*.log | sort | uniq -c
```

---

## Troubleshooting

### Agent Crashes (Process Exits)

Check logs:
```bash
tail -50 logs/agent_{INDEX}.log | grep -i error
```

Common issues:
- Wallet not funded (nonce fetch fails)
- Invalid ADMIN_WALLET_ADDRESS (sender whitelist rejects)
- Network timeout (API down)

**Fix**: Restart orchestrator (will respawn agents).

### Nonce Desync

If an agent's local nonce diverges from blockchain:
- Agent stops sending (validation fails)
- Check log: `Bulk aborted after X/95 txs`

**Fix**: Already handled! Agent re-syncs automatically on abort.

### Concurrent Command Race

If both agents start sending on the same GREEN:
- Both react independently (intended!)
- No collision — different nonces
- Score aggregated correctly

---

## Performance Tuning

### Increase Throughput (Advanced)

Edit `challenge5.config.json`:

```json
{
  "BULK": {
    "MAX_PARALLEL_TXS": 50,      // ↑ from 30 (more concurrent)
    "TX_INTERVAL_MS": 10,         // ↓ from 50 (faster dispatch)
    "GAS_PRICE_MULTIPLIER": 4.0   // Keep at 4x for priority
  }
}
```

**Impact**: 95 txs per bulk sent in ~5s instead of ~10s.

### Reduce Cost

If approaching budget limit:
```json
{
  "BULK": {
    "GAS_PRICE_MULTIPLIER": 2.0   // ↓ from 4x (save 50% gas)
  }
}
```

**Risk**: Txs take longer to finalize (may not beat RED deadline).

---

## Cost Analysis

**Per agent (50 EGLD budget)**:

- 95 txs × 50k gas × 4 GWei = 0.019 EGLD per bulk
- 50 EGLD ÷ 0.019 = ~2,600 bulks per agent
- 2 rounds × 30 min = 60 min
- Assuming 2 GREEN windows per round = ~4 GREEN windows total
- If each GREEN lasts 10 min = 40 min of sending
- 40 min ÷ 5s per bulk = 480 bulks possible
- **Each agent can send 480 × 95 = 45,600 txs max**

**For 10 agents**:
- Total throughput: 456,000 txs (if perfectly timed)
- Budget: 10 × 50 = 500 EGLD (fully utilized, 100%)

---

## Shutdown

When challenge ends (17:30 UTC or manual stop):

```bash
# Graceful shutdown
Ctrl+C

# Orchestrator will:
# 1. Send SIGTERM to all 10 agents
# 2. Wait 2 seconds for graceful exit
# 3. Force SIGKILL any remaining
# 4. Finalize logs
```

Check final scores on leaderboard immediately after.

---

## FAQ

**Q: Can I run fewer than 10 agents?**

A: Yes. Use `--agents 5` instead. But 10 is the max allowed by rules.

**Q: Can agents run on different machines?**

A: Not with this orchestrator (it spawns subprocesses). For distributed: deploy manually to each machine, set env vars, run `npm run challenge5:start`.

**Q: What if one agent crashes?**

A: The other 9 keep running. The crashed agent's score doesn't count for that period. Restart orchestrator to respawn all.

**Q: How do I verify all 10 are registered?**

A: Check `wallets/registration.json`. All 10 should have nonce ≥ 1.

**Q: Can I use different gas prices per agent?**

A: No. All agents share `challenge5.config.json`. To customize per-agent, manually edit generated config files before launch.

---

## Next Steps (After Challenge)

1. **Collect logs**: `tar czf challenge5-logs.tar.gz logs/`
2. **Analyze performance**: `jq '.type' logs/agent_*.log | sort | uniq -c`
3. **Submit to portal**: Include `wallets/registration.json` + agent addresses
4. **Post recap**: GitHub + Twitter with final scores

---

**Good luck. Build smart. React fast.** ⚡

