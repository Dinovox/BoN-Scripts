/**
 * Challenge 5 — Agent Loop
 *
 * Orchestrates the command listener and bulk sender via a state machine:
 *
 *   STOPPED → LISTENING → SENDING → LISTENING → STOPPED
 *
 * - Starts listener immediately on launch
 * - Begins bulk sends as soon as GREEN command is received
 * - Immediately aborts sending on RED command (kill switch)
 * - Logs all events to file + stdout for post-challenge analysis
 *
 * Usage:
 *   npx ts-node src/challenge5-agent.ts
 *   (or via npm run challenge5:start)
 */

import * as fs from 'fs';
import * as path from 'path';
import {getSigner} from './utils/txUtils';
import {SemanticInterpreter} from './challenge5-interpreter';
import {CommandListener, CommandEvent} from './challenge5-listener';
import {BulkSender, BulkResult} from './challenge5-bulk';
import {Logger, LogLevel} from './utils/logger';

const log = new Logger('Challenge5:Agent', LogLevel.DEBUG);

// ─── Types ────────────────────────────────────────────────────────────────────

export type AgentState = 'STOPPED' | 'LISTENING' | 'SENDING';

interface Challenge5Config {
  ADMIN_WALLET_ADDRESS: string;
  TARGET_WALLET_ADDRESS: string;
  NETWORK: {
    API_URL: string;
    CHAIN_ID: string;
    BLOCK_TIME_MS: number;
    TX_POLL_INTERVAL_MS: number;
  };
  BULK: {
    BULK_SIZE: number;
    GAS_PRICE_MULTIPLIER: number;
    GAS_PRICE_BASE: number;
    GAS_LIMIT_PER_TX: number;
    TX_INTERVAL_MS: number;
    MAX_PARALLEL_TXS: number;
    TX_VALUE_EGLD: string;
    TX_DATA: string;
  };
  RETRY: {
    MAX_ATTEMPTS: number;
    RETRY_DELAY_MS: number;
    TX_TIMEOUT_MS: number;
  };
  LISTENER: {
    POLL_INTERVAL_MS: number;
    MAX_TX_AGE_MS: number;
    LOOKBACK_TX_COUNT: number;
  };
  INTERPRETER: {
    CONFIDENCE_THRESHOLD: number;
    ADVERSARIAL_DETECTION: boolean;
    ADVERSARIAL_PENALTY: number;
  };
  LOGGING: {
    LEVEL: string;
    LOG_FILE: string;
    LOG_TX_HASHES: boolean;
  };
}

// ─── Agent ────────────────────────────────────────────────────────────────────

export class Challenge5Agent {
  private state: AgentState = 'STOPPED';
  private config: Challenge5Config;
  private listener: CommandListener | null = null;
  private bulkSender: BulkSender | null = null;
  private logStream: fs.WriteStream | null = null;
  private activeBulkAbort: AbortController | null = null;
  private bulkRunCount = 0;
  private greenStartTime = 0;
  private handlingCommand = false; // Prevent concurrent command handlers (race condition fix)

  constructor(config: Challenge5Config) {
    this.config = config;
  }

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  async start(): Promise<void> {
    if (this.state !== 'STOPPED') {
      log.warn('Agent already running');
      return;
    }

    this.initLogger();
    this.logEvent('AGENT_START', {config: this.sanitizedConfig()});

    // Validate required addresses
    if (!this.config.TARGET_WALLET_ADDRESS) {
      throw new Error(
        '❌ TARGET_WALLET_ADDRESS is empty. Fill challenge5.config.json before launching.',
      );
    }

    log.info('🚀 Challenge 5 Agent starting…');
    log.info(`Target: ${this.config.TARGET_WALLET_ADDRESS}`);

    // Initialize components
    const signer = getSigner();
    const senderAddress = signer.getAddress().bech32();
    log.info(`Sender wallet: ${senderAddress}`);

    const interpreter = new SemanticInterpreter(
      this.config.INTERPRETER.CONFIDENCE_THRESHOLD,
      this.config.INTERPRETER.ADVERSARIAL_PENALTY,
      this.config.INTERPRETER.ADVERSARIAL_DETECTION,
    );

    this.listener = new CommandListener(
      {
        targetAddress: this.config.TARGET_WALLET_ADDRESS,
        apiUrl: this.config.NETWORK.API_URL,
        pollIntervalMs: this.config.LISTENER.POLL_INTERVAL_MS,
        maxTxAgeMs: this.config.LISTENER.MAX_TX_AGE_MS,
        lookbackTxCount: this.config.LISTENER.LOOKBACK_TX_COUNT,
        confidenceThreshold: this.config.INTERPRETER.CONFIDENCE_THRESHOLD,
        adminAddress: this.config.ADMIN_WALLET_ADDRESS, // Whitelist: only accept commands from admin
      },
      interpreter,
    );

    this.bulkSender = new BulkSender(
      {
        targetAddress: this.config.TARGET_WALLET_ADDRESS,
        chainId: this.config.NETWORK.CHAIN_ID,
        apiUrl: this.config.NETWORK.API_URL,
        bulkSize: this.config.BULK.BULK_SIZE,
        gasPriceMultiplier: this.config.BULK.GAS_PRICE_MULTIPLIER,
        gasPriceBase: this.config.BULK.GAS_PRICE_BASE,
        gasLimitPerTx: this.config.BULK.GAS_LIMIT_PER_TX,
        txIntervalMs: this.config.BULK.TX_INTERVAL_MS,
        maxParallelTxs: this.config.BULK.MAX_PARALLEL_TXS,
        txValue: this.config.BULK.TX_VALUE_EGLD,
        txData: this.config.BULK.TX_DATA,
        maxRetryAttempts: this.config.RETRY.MAX_ATTEMPTS,
        retryDelayMs: this.config.RETRY.RETRY_DELAY_MS,
        txTimeoutMs: this.config.RETRY.TX_TIMEOUT_MS,
      },
      signer,
    );

    // Initialize nonce tracking for continuous bulks (critical!)
    await this.bulkSender.initializeNonce();

    // Wire up event handlers
    this.listener.on('commandReceived', async (event: CommandEvent) => {
      // Prevent concurrent command handlers (race condition fix)
      if (this.handlingCommand) {
        log.warn('⚠️ Already handling a command — ignoring concurrent call');
        return;
      }
      this.handlingCommand = true;
      try {
        await this.handleCommand(event);
      } catch (err) {
        log.error(`Command handler error: ${(err as Error).message}`);
        this.logEvent('COMMAND_ERROR', {error: (err as Error).message});
      } finally {
        this.handlingCommand = false;
      }
    });

    this.listener.on('error', (err: Error) => {
      log.error(`Listener error: ${err.message}`);
      this.logEvent('LISTENER_ERROR', {error: err.message});
    });

    // Start listening
    this.transition('LISTENING');
    await this.listener.start();

    log.info('✅ Agent ready. Waiting for GREEN command…');

    // Graceful shutdown handlers
    process.on('SIGINT', () => this.shutdown('SIGINT'));
    process.on('SIGTERM', () => this.shutdown('SIGTERM'));
  }

  async stop(): Promise<void> {
    await this.shutdown('manual');
  }

  getState(): AgentState {
    return this.state;
  }

  // ── State Machine ──────────────────────────────────────────────────────────

  /** Transitions back to LISTENING if we're not already there (avoids TS narrowing conflicts). */
  private transitionToListeningIfNeeded(): void {
    if ((this.state as string) !== 'LISTENING') {
      this.transition('LISTENING');
    }
  }

  private transition(newState: AgentState): void {
    const prev = this.state;
    this.state = newState;
    log.info(`State: ${prev} → ${newState}`);
    this.logEvent('STATE_TRANSITION', {from: prev, to: newState});
  }

  private async handleCommand(event: CommandEvent): Promise<void> {
    const {intent, confidence, reasoning, adversarial} = event.interpretation;

    log.info(
      `📨 Command received: ${intent} (${(confidence * 100).toFixed(0)}%) from ${event.sender.slice(0, 16)}…`,
    );
    log.debug(`  Reasoning: ${reasoning}`);
    if (adversarial) {
      log.warn(`  ⚠️ ADVERSARIAL INPUT DETECTED — ${reasoning}`);
    }

    this.logEvent('COMMAND_RECEIVED', {
      txHash: event.txHash,
      sender: event.sender,
      intent,
      confidence,
      reasoning,
      adversarial,
      data: event.data,
    });

    if (intent === 'GREEN') {
      // Fire-and-forget: do NOT await — bulk loop runs in background
      // so handlingCommand is released immediately and RED can be processed
      this.onGreenCommand(event).catch(err =>
        log.error(`GREEN handler error: ${(err as Error).message}`),
      );
    } else if (intent === 'RED') {
      await this.onRedCommand(event);
    }
  }

  private async onGreenCommand(_event: CommandEvent): Promise<void> {
    if (this.state === 'SENDING') {
      log.info('Already SENDING — resetting GREEN timer for fresh burst window');
      this.greenStartTime = Date.now();
      return;
    }

    log.info('🟢 GREEN command received — starting continuous bulk send!');
    this.transition('SENDING' as AgentState);
    this.activeBulkAbort = new AbortController();
    this.greenStartTime = Date.now();
    this.bulkSender!.resetNonce(); // Force nonce re-sync from blockchain at start of each GREEN session

    const bulkAbort = this.activeBulkAbort;
    const isSending = () => this.state === 'SENDING';

    try {
      // Continuous bulk loop: keep sending 95-tx batches until RED or abort
      while (isSending() && !bulkAbort.signal.aborted) {
        const bulkIdx = ++this.bulkRunCount;
        const elapsed = Date.now() - this.greenStartTime;
        const fullSpeedMs = 25_000;
        const decayedSize = elapsed < fullSpeedMs
          ? this.config.BULK.BULK_SIZE
          : Math.max(20, Math.floor(this.config.BULK.BULK_SIZE * Math.pow(0.85, (elapsed - fullSpeedMs) / 2000)));
        log.info(
          `🚀 Bulk #${bulkIdx}: ${decayedSize} txs at ${this.config.BULK.GAS_PRICE_MULTIPLIER}x gas (${Math.round(elapsed / 1000)}s into GREEN)`,
        );

        try {
          const result = await this.bulkSender!.sendBulk(bulkAbort.signal, decayedSize);
          this.logBulkResult(bulkIdx, result);

          if (!bulkAbort.signal.aborted) {
            log.info(
              `✅ Bulk #${bulkIdx} done: ${result.confirmed}/${result.sent} txs in ${result.durationMs}ms | Continuing…`,
            );
            // Small delay before next bulk to let nonce settle
            await this.sleep(100);
          } else {
            log.info(`Bulk #${bulkIdx} aborted by RED command`);
            break;
          }
        } catch (bulkErr) {
          if ((bulkErr as Error).message === 'Aborted') {
            log.info(`Bulk #${bulkIdx} aborted`);
            break;
          } else {
            log.error(`Bulk #${bulkIdx} error: ${(bulkErr as Error).message}`);
            this.logEvent('BULK_ERROR', {
              bulkIdx,
              error: (bulkErr as Error).message,
            });
            // On error, wait a bit and retry
            await this.sleep(500);
          }
        }
      }

      if (bulkAbort.signal.aborted) {
        log.info(`Bulk loop aborted by RED command`);
      }
      this.transition('LISTENING');
    } catch (err) {
      log.error(`Bulk loop error: ${(err as Error).message}`);
      this.logEvent('BULK_LOOP_ERROR', {error: (err as Error).message});
      this.transitionToListeningIfNeeded();
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  private async onRedCommand(event: CommandEvent): Promise<void> {
    log.info('🔴 RED command received — kill switch activated!');
    this.logEvent('RED_COMMAND', {txHash: event.txHash, sender: event.sender});

    // Immediate abort of active bulk
    if (this.activeBulkAbort && !this.activeBulkAbort.signal.aborted) {
      this.activeBulkAbort.abort();
      this.bulkSender?.abort();
      log.info('⛔ Active bulk send aborted');
    }

    if (this.state !== 'STOPPED') {
      this.transition('LISTENING');
    }
  }

  // ── Shutdown ───────────────────────────────────────────────────────────────

  private async shutdown(reason: string): Promise<void> {
    log.info(`Shutting down (${reason})…`);
    this.logEvent('AGENT_SHUTDOWN', {reason});

    // Stop bulk if running
    if (this.activeBulkAbort) {
      this.activeBulkAbort.abort();
    }
    this.bulkSender?.abort();

    // Stop listener
    this.listener?.stop();

    this.transition('STOPPED');
    this.logStream?.end();

    log.info('Goodbye!');
    process.exit(0);
  }

  // ── Logging ────────────────────────────────────────────────────────────────

  private initLogger(): void {
    const logFile = this.config.LOGGING.LOG_FILE || 'challenge5.log';
    const logPath = path.isAbsolute(logFile)
      ? logFile
      : path.resolve(process.cwd(), logFile);

    this.logStream = fs.createWriteStream(logPath, {flags: 'a'});
    log.info(`Event log: ${logPath}`);
  }

  private logEvent(type: string, data: Record<string, unknown>): void {
    if (!this.logStream) return;
    const entry = JSON.stringify({
      ts: new Date().toISOString(),
      type,
      ...data,
    });
    this.logStream.write(entry + '\n');
  }

  private logBulkResult(bulkIdx: number, result: BulkResult): void {
    this.logEvent('BULK_COMPLETE', {
      bulkIdx,
      sent: result.sent,
      confirmed: result.confirmed,
      failed: result.failed,
      retried: result.retried,
      durationMs: result.durationMs,
      estimatedFinalizationMs: result.estimatedFinalizationMs,
      txHashes: this.config.LOGGING.LOG_TX_HASHES ? result.txHashes : `[${result.txHashes.length} hashes]`,
      failedHashes: result.failedHashes,
    });
  }

  private sanitizedConfig(): Record<string, unknown> {
    return {
      TARGET_WALLET_ADDRESS: this.config.TARGET_WALLET_ADDRESS
        ? `${this.config.TARGET_WALLET_ADDRESS.slice(0, 12)}…`
        : '[not set]',
      BULK_SIZE: this.config.BULK.BULK_SIZE,
      GAS_PRICE_MULTIPLIER: this.config.BULK.GAS_PRICE_MULTIPLIER,
      MAX_PARALLEL_TXS: this.config.BULK.MAX_PARALLEL_TXS,
      POLL_INTERVAL_MS: this.config.LISTENER.POLL_INTERVAL_MS,
    };
  }
}

// ─── Entry Point ─────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  // Load config
  const configPath = path.resolve(
    __dirname,
    '../challenge5.config.json',
  );

  if (!fs.existsSync(configPath)) {
    console.error(`❌ Config not found: ${configPath}`);
    process.exit(1);
  }

  const config: Challenge5Config = JSON.parse(
    fs.readFileSync(configPath, 'utf8'),
  );

  // Validate critical fields
  if (!config.TARGET_WALLET_ADDRESS) {
    console.error(
      '❌ TARGET_WALLET_ADDRESS is empty in challenge5.config.json',
    );
    console.error('   Set it to the target wallet address before launching.');
    process.exit(1);
  }

  const agent = new Challenge5Agent(config);
  await agent.start();

  // Keep process alive
  await new Promise(() => {}); // Never resolves — agent runs until SIGINT/SIGTERM
}

if (require.main === module) {
  main().catch(err => {
    console.error('Fatal error:', err);
    process.exit(1);
  });
}
