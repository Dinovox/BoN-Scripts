# Challenge 5 — Agent Arena

## Overview

**Challenge 5** is a real-time autonomous trading challenge where your agent must:
1. **Listen** to admin wallet commands (loose natural language)
2. **Interpret** intent (GREEN = send transactions, RED = stop immediately)
3. **React instantly** — bulk send 95 transactions per batch when permitted
4. **Maximize score** — Permitted TXs − Unpermitted TXs

## Scoring Formula

```
Score = TotalPermittedTxs − TotalUnpermittedTxs
Guild Score = Sum of all agent scores
```

**Key insight**: Sending during RED-light windows costs you. Precision > Volume.

## Timeline

| Time (UTC) | Event | Action |
|---|---|---|
| 15:00 | Funds + Wallet announcement | Configure admin/target addresses in `challenge5.config.json` |
| 15:45 | Agent registration deadline | Agent already registered (nonce 1) |
| 16:00 | **Round 1 starts** | `npm run challenge5:start` → Listen + React |
| 16:30 | Round 1 ends | Review performance, tune if needed |
| 17:00 | **Round 2 starts** | Resume with same agent |
| 17:30 | Challenge closes | Final scores computed |

## Pre-Challenge Checklist (Before 15:00 UTC)

- [x] Agent registered (nonce 1)
- [x] Wallet funded (2 EGLD)
- [x] Bulk transaction pipeline built
- [x] Command listener ready
- [x] Semantic interpreter implemented
- [ ] **15:00 UTC**: Update `challenge5.config.json` with:
  - `wallets.admin` ← announced address
  - `wallets.target` ← announced address
- [ ] **15:45 UTC**: Confirm all systems ready, no agent re-registration needed

## Running the Agent

### Start Challenge 5 Agent

```bash
cd moltbot-starter-kit

# Set env variables (should already be in .env)
export MULTIVERSX_PRIVATE_KEY=wallet.pem
export MULTIVERSX_NETWORK=battleofnodes
export MULTIVERSX_CHAIN_ID=B
export MULTIVERSX_API_URL=https://api.battleofnodes.com

# Launch the agent
npm run challenge5:start

# Or with explicit config:
ADMIN_WALLET="erd1..." \
TARGET_WALLET="erd1..." \
npm run challenge5:start
```

### Dry Run (Local Testing)

```bash
# Test command listener + interpreter without sending real txs
npm run challenge5:test

# Simulate adversarial commands
npm run challenge5:test -- --scenario adversarial
```

## Architecture

### 1. Command Listener (`src/challenge5-listener.ts`)

- **Polls** TARGET wallet for incoming transactions from ADMIN wallet
- **Extracts** data field (plain text commands)
- **Fires** events: `commandReceived`, `stateChange`
- **Monitors** command timing (min 10 seconds between commands)

**Exposed**:
```typescript
class CommandListener {
  onCommandReceived: (cmd: Command) => Promise<void>;
  start(): Promise<void>;
  stop(): Promise<void>;
  getLastCommand(): Command | null;
}
```

### 2. Semantic Interpreter (`src/challenge5-interpreter.ts`)

- **Analyzes** command text for intent (GREEN / RED / UNKNOWN)
- **Detects** adversarial patterns (misleading, ambiguous language)
- **Scores** confidence (0-1) for each interpretation
- **Logs** reasoning for post-challenge analysis

**Exposed**:
```typescript
class SemanticInterpreter {
  interpret(command: string): InterpretationResult;
  // Returns: { intent, confidence, reasoning }
}
```

### 3. Bulk Transaction Pipeline (`src/challenge5-bulk.ts`)

- **Builds** batches of 95 transactions
- **Sets** high gas price (1.5x default) for finalization guarantee
- **Sends** in parallel (max 20 concurrent)
- **Retries** failed txs (3 attempts with backoff)
- **Tracks** all pending txs (cancellable on RED)

**Exposed**:
```typescript
class BulkSender {
  sendBulk(count: number): Promise<TransactionResult[]>;
  pause(): Promise<void>;
  cancel(): Promise<void>;
  getStats(): SendStats;
}
```

### 4. Agent Loop (`src/challenge5-agent.ts`)

- **State machine**: STOPPED → LISTENING → SENDING → (RED) → STOPPED
- **Coordinates** listener + interpreter + bulk sender
- **Immediate kill switch** on RED (max 600ms reaction time)
- **Detailed logging** for post-challenge review

**Flow**:
```
[LISTENING]
    ↓ (command received)
[INTERPRET] → GREEN?
    ↓ YES
[SENDING] → Launch bulk (95 tx batches)
    ↓ (next command)
[INTERPRET] → RED?
    ↓ YES
[KILL SWITCH] → Cancel pending, stop new sends
    ↓
[LISTENING] (wait for next GREEN)
```

## Configuration

Edit `challenge5.config.json`:

| Key | Default | Tuning |
|---|---|---|
| `bulkSize` | 95 | DO NOT CHANGE (fixed by challenge rules) |
| `gasPrice` | 1.5e9 | Increase if txs not finalizing (1.5x-2x default) |
| `gasPriceMultiplier` | 1.5 | Auto-scale gas price relative to network default |
| `txIntervalMs` | 100 | Decrease for faster bulk send (risk: mempool congestion) |
| `maxParallelTxs` | 20 | Increase for more concurrency (risk: rate limits) |
| `commandCheckIntervalMs` | 500 | Decrease for faster command detection (~300ms min) |

**Conservative defaults** — tune aggressively only if you see timeouts.

## Scoring Strategy

### Window Timing

- **RED window**: You sent 10 txs = −10 points
- **GREEN window**: You sent 95 txs = +95 points
- **UNKNOWN**: Interpreter unsure? Default to STOP (conservative)

### Bulk Optimization

1. **Batch 95 txs immediately** when GREEN detected
2. **Use high gas price** to guarantee finalization before next RED
3. **Monitor block time** (600ms on BoN)
4. **Estimate finalization**: 95 txs ÷ 600ms block time ≈ 3-4 blocks
5. **Stop sending** 1-2 blocks before expected RED command

### Adversarial Defense

The admin will use loose language:
- "go go go" → GREEN
- "stop pls" → RED
- "ok I think we should pause" → RED (ambiguous, but safer to assume)
- "keep going friend" → GREEN
- "maybe slow down?" → UNKNOWN → default STOP (conservative)

**Interpreter** uses:
- **Keyword patterns** (fast, for obvious commands)
- **Semantic scoring** (LLM-style, for ambiguous cases)
- **Confidence threshold** (fallback to conservative if <0.7)

## Testing

### Unit Tests

```bash
# Test interpreter against known commands
npm run challenge5:test -- --unit

# Test bulk sender pipeline
npm run challenge5:test -- --bulk

# Test listener polling
npm run challenge5:test -- --listener
```

### Integration Tests

```bash
# Simulate a full round with mock admin commands
npm run challenge5:test -- --integration --round 1

# Stress test with fast command changes
npm run challenge5:test -- --stress --rapid-commands
```

### Fixtures

See `tests/challenge5-fixtures.ts` for:
- Valid GREEN commands
- Valid RED commands
- Adversarial / misleading commands
- Network latency simulations

## Debugging

### Live Logs

```bash
DEBUG=challenge5:* npm run challenge5:start
```

### Post-Challenge Review

All transactions are logged to `challenge5-round1.log` and `challenge5-round2.log`:
- Command timestamps
- Interpretation results (intent + confidence)
- All transaction hashes (sent, failed, cancelled)
- Final score metrics

### Inspector Tool

```bash
npm run challenge5:inspect -- --round 1 --agent erd100v...
```

Outputs:
- Timeline of all commands + reactions
- Score breakdown (permitted vs unpermitted)
- Gas spent (budget check)
- Finalization times (were txs fast enough?)

## Performance Targets

| Metric | Target |
|---|---|
| Command → First TX latency | <100ms |
| Full bulk finalization | <2.5s (4-5 blocks) |
| Uptime (no crashes) | 100% |
| Permitted TXs / total | >95% (precision > volume) |
| Gas spent | <500 EGLD (shared budget) |

## Known Limitations

1. **NLP Interpreter** is rule-based (regex + scoring), not a full LLM
   - Handles most obvious cases well
   - Ambiguous commands → defaults to STOP (conservative)
   - Can be improved with actual LLM if needed

2. **Transaction finalization** assumes 600ms block time
   - If network is slower, increase gas price or reduce bulk size
   - Monitor live block time on explorer

3. **Kill switch latency** is ~100ms (network + local processing)
   - BoN block time (600ms) >> kill switch latency, so OK

## Next Steps (Tomorrow 15:00 UTC)

1. **Receive announcement**: Admin + TARGET wallet addresses
2. **Update config**:
   ```bash
   # Edit challenge5.config.json
   {
     "wallets": {
       "admin": "erd1...",  // ← Insert announced address
       "target": "erd1..."  // ← Insert announced address
     }
   }
   ```
3. **Start agent**:
   ```bash
   npm run challenge5:start
   ```
4. **Monitor live**:
   - Open explorer: https://explorer.battleofnodes.com
   - Search agent wallet for outgoing transactions
   - Check Discord/Telegram for score updates

## Resources

- **Challenge spec**: See `CHALLENGE5_README.md` (this file)
- **Config**: `challenge5.config.json`
- **Tests**: `tests/challenge5.test.ts`
- **Live scores**: https://bon.multiversx.com/guild-wars
- **Network explorer**: https://explorer.battleofnodes.com

---

**Good luck. Build smart. React fast.** ⚡
