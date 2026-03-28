/**
 * Challenge 5 — Command Listener
 *
 * Polls the MultiversX API for incoming transactions on the TARGET wallet.
 * Parses the data field of each new transaction through the SemanticInterpreter
 * and emits "commandReceived" events with intent + confidence.
 *
 * Usage:
 *   const listener = new CommandListener(config, interpreter);
 *   listener.on('commandReceived', (event) => { ... });
 *   await listener.start();
 *   await listener.stop();
 */

import {EventEmitter} from 'events';
import * as fs from 'fs';
import {SemanticInterpreter, InterpretResult} from './challenge5-interpreter';
import {Logger, LogLevel} from './utils/logger';

const COMMANDS_LOG = 'commands.log';
function appendCommandLog(line: string): void {
  fs.appendFileSync(COMMANDS_LOG, line + '\n');
}

const log = new Logger('Challenge5:Listener', LogLevel.DEBUG);

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ListenerConfig {
  targetAddress: string;
  apiUrl: string;
  pollIntervalMs: number;
  maxTxAgeMs: number;
  lookbackTxCount: number;
  confidenceThreshold: number;
  adminAddress?: string; // Only accept commands from this address (whitelist)
}

export interface CommandEvent {
  txHash: string;
  sender: string;
  timestamp: number;
  data: string;
  interpretation: InterpretResult;
}

interface ApiTransaction {
  txHash: string;
  sender: string;
  receiver: string;
  data?: string;
  timestamp: number;
  status: string;
}

// ─── CommandListener ─────────────────────────────────────────────────────────

export class CommandListener extends EventEmitter {
  private config: ListenerConfig;
  private interpreter: SemanticInterpreter;
  private running = false;
  private pollTimer: NodeJS.Timeout | null = null;
  private seenTxHashes = new Set<string>();
  private lastPollTime = 0;
  private adminAddress?: string; // Whitelisted command sender

  constructor(config: ListenerConfig, interpreter: SemanticInterpreter) {
    super();
    this.config = config;
    this.interpreter = interpreter;
    this.adminAddress = config.adminAddress; // Store admin address for filtering
  }

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  async start(): Promise<void> {
    if (this.running) {
      log.warn('Listener already running');
      return;
    }
    if (!this.config.targetAddress) {
      throw new Error(
        'TARGET_WALLET_ADDRESS is not set in challenge5.config.json',
      );
    }

    this.running = true;
    this.lastPollTime = Date.now();
    log.info(
      `Starting listener on ${this.config.targetAddress} (poll every ${this.config.pollIntervalMs}ms)`,
    );
    this.emit('started');
    await this.poll(); // Immediate first poll
    this.schedulePoll();
  }

  stop(): void {
    if (!this.running) return;
    this.running = false;
    if (this.pollTimer) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
    log.info('Listener stopped');
    this.emit('stopped');
  }

  isRunning(): boolean {
    return this.running;
  }

  // ── Internal Polling ───────────────────────────────────────────────────────

  private schedulePoll(): void {
    if (!this.running) return;
    this.pollTimer = setTimeout(async () => {
      try {
        await this.poll();
      } catch (err) {
        log.error(`Poll error: ${(err as Error).message}`);
        this.emit('error', err);
      }
      this.schedulePoll();
    }, this.config.pollIntervalMs);
  }

  private async poll(): Promise<void> {
    const transactions = await this.fetchRecentTransactions();
    const now = Date.now();
    const cutoff = now - this.config.maxTxAgeMs;

    // API returns desc (newest first) — reverse to process oldest→newest
    // so the most recent command is the last one processed (correct final state)
    for (const tx of [...transactions].reverse()) {
      // Skip already processed
      if (this.seenTxHashes.has(tx.txHash)) continue;
      // Skip too old
      const txTime = tx.timestamp * 1000; // Convert seconds → ms
      if (txTime < cutoff) continue;
      // Skip failed txs
      if (tx.status !== 'success') continue;
      // Skip txs with no data
      if (!tx.data) continue;
      // Skip txs from untrusted senders (whitelist check)
      if (this.adminAddress && tx.sender !== this.adminAddress) {
        log.debug(
          `🚫 Ignoring command from untrusted sender: ${tx.sender.slice(0, 16)}…`,
        );
        continue;
      }

      this.seenTxHashes.add(tx.txHash);

      // Prevent memory leak: prune set if > 10k entries
      if (this.seenTxHashes.size > 10_000) {
        const iter = this.seenTxHashes.values();
        for (let i = 0; i < 1000; i++) this.seenTxHashes.delete(iter.next().value as string);
      }

      const decodedData = decodeBase64Field(tx.data);
      const interpretation = await this.interpreter.interpretAsync(decodedData);

      const colorIcon = interpretation.intent === 'GREEN' ? '🟢' : interpretation.intent === 'RED' ? '🔴' : '⚪';
      const cmdLine = `${colorIcon} ${interpretation.intent.padEnd(7)} | ${tx.txHash.slice(0, 20)}… | "${decodedData.slice(0, 80)}"`;
      console.log(cmdLine);
      appendCommandLog(cmdLine);

      if (interpretation.intent !== 'UNKNOWN') {
        const event: CommandEvent = {
          txHash: tx.txHash,
          sender: tx.sender,
          timestamp: tx.timestamp,
          data: decodedData,
          interpretation,
        };
        log.info(
          `✅ Command received: ${interpretation.intent} (confidence=${interpretation.confidence}) from ${tx.sender.slice(0, 12)}…`,
        );
        this.emit('commandReceived', event);
      } else {
        log.debug(
          `⚪ UNKNOWN intent in TX ${tx.txHash.slice(0, 16)}… — skipping`,
        );
      }
    }

    this.lastPollTime = now;
  }

  // ── API Calls ─────────────────────────────────────────────────────────────

  private async fetchRecentTransactions(): Promise<ApiTransaction[]> {
    const senderFilter = this.adminAddress ? `&sender=${this.adminAddress}` : '';
    const url = `${this.config.apiUrl}/transactions?receiver=${this.config.targetAddress}${senderFilter}&size=${this.config.lookbackTxCount}&order=desc`;

    let resp: Response;
    try {
      resp = await fetch(url, {signal: AbortSignal.timeout(10_000)});
    } catch (err) {
      throw new Error(`Failed to fetch transactions: ${(err as Error).message}`);
    }

    if (!resp.ok) {
      throw new Error(`API error ${resp.status} fetching transactions`);
    }

    const data = (await resp.json()) as ApiTransaction[];
    return Array.isArray(data) ? data : [];
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Decodes a MultiversX on-chain data field (base64 encoded).
 */
export function decodeBase64Field(raw: string): string {
  try {
    return Buffer.from(raw, 'base64').toString('utf8');
  } catch {
    return raw;
  }
}

// ─── Factory ─────────────────────────────────────────────────────────────────

export function createCommandListener(
  config: ListenerConfig,
  interpreter: SemanticInterpreter,
): CommandListener {
  return new CommandListener(config, interpreter);
}
