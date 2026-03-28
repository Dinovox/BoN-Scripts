/**
 * Challenge 5 — Bulk Transaction Module
 *
 * Sends batches of up to 95 transactions in parallel with elevated gas price
 * for network priority. Handles automatic retries for failed transactions.
 *
 * Key features:
 * - Configurable bulk size (default 95)
 * - Gas price multiplier (1.5x–2x default)
 * - Sliding nonce management (no race conditions)
 * - Retry logic with exponential backoff
 * - Finalization time estimation based on gas
 * - Abort signal support for immediate kill-switch
 */

import {Transaction, Address, TransactionComputer} from '@multiversx/sdk-core';
import {UserSigner} from '@multiversx/sdk-wallet';
import {ApiNetworkProvider} from '@multiversx/sdk-network-providers';
import {Logger, LogLevel} from './utils/logger';

const log = new Logger('Challenge5:Bulk', LogLevel.DEBUG);

// ─── Types ────────────────────────────────────────────────────────────────────

export interface BulkConfig {
  targetAddress: string;
  chainId: string;
  apiUrl: string;
  bulkSize: number;
  gasPriceMultiplier: number;
  gasPriceBase: number;
  gasLimitPerTx: number;
  txIntervalMs: number;
  maxParallelTxs: number;
  txValue: string; // in atomic EGLD (denomination)
  txData: string;
  maxRetryAttempts: number;
  retryDelayMs: number;
  txTimeoutMs: number;
}

export interface BulkResult {
  sent: number;
  confirmed: number;
  failed: number;
  retried: number;
  txHashes: string[];
  failedHashes: string[];
  durationMs: number;
  estimatedFinalizationMs: number;
}

export interface TxRecord {
  nonce: number;
  hash: string;
  status: 'pending' | 'success' | 'failed' | 'unknown';
  attempts: number;
  lastError?: string;
}

// ─── Gas & Finalization Estimates ─────────────────────────────────────────────

/**
 * Calculates estimated finalization time (ms) based on gas price.
 * Higher gas = more likely to be included in next block (6s on devnet).
 *
 * Formula:
 *   basePriority = gasPrice / defaultGasPrice
 *   If priority >= 2.0 → 1 block (6s)
 *   If priority >= 1.5 → 2 blocks (12s)
 *   If priority >= 1.0 → 3 blocks (18s)
 *   Otherwise → 5 blocks (30s)
 */
export function estimateFinalizationMs(
  gasPrice: number,
  defaultGasPrice = 1_000_000_000,
  blockTimeMs = 6000,
): number {
  const priority = gasPrice / defaultGasPrice;
  if (priority >= 2.0) return blockTimeMs;
  if (priority >= 1.5) return blockTimeMs * 2;
  if (priority >= 1.0) return blockTimeMs * 3;
  return blockTimeMs * 5;
}

// ─── BulkSender ──────────────────────────────────────────────────────────────

export class BulkSender {
  private config: BulkConfig;
  private signer: UserSigner;
  private provider: ApiNetworkProvider;
  private aborted = false;
  private abortController: AbortController | null = null;
  private computer = new TransactionComputer();
  private localNonce: number = -1; // Tracks nonce locally to avoid race conditions

  constructor(config: BulkConfig, signer: UserSigner) {
    this.config = config;
    this.signer = signer;
    this.provider = new ApiNetworkProvider(config.apiUrl, {
      clientName: 'challenge5-bulk',
    });
  }

  /**
   * Initialize local nonce tracking (call once before first sendBulk).
   */
  async initializeNonce(): Promise<void> {
    const senderBech32 = this.signer.getAddress().bech32();
    const senderForProvider = {bech32: () => senderBech32};
    const account = await this.provider.getAccount(senderForProvider as {bech32: () => string});
    this.localNonce = account.nonce;
    log.info(`🔔 Local nonce initialized: ${this.localNonce}`);
  }

  /**
   * Force nonce re-sync on next sendBulk call.
   * Call this at the start of each new GREEN session to avoid drift.
   */
  resetNonce(): void {
    this.localNonce = -1;
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /**
   * Send a full bulk batch. Resolves when all transactions are dispatched
   * (not necessarily confirmed — use waitForConfirmations for that).
   */
  async sendBulk(abortSignal?: AbortSignal, overrideSize?: number): Promise<BulkResult> {
    const effectiveSize = overrideSize ?? this.config.bulkSize;
    const start = Date.now();
    this.aborted = false;
    this.abortController = new AbortController();

    if (!this.config.targetAddress) {
      throw new Error('TARGET_WALLET_ADDRESS not configured');
    }

    const senderBech32 = this.signer.getAddress().bech32();
    // Providers package uses IAddress with .bech32() — use a compat shim
    const senderForProvider = {bech32: () => senderBech32};

    log.info(
      `Sending bulk of ${effectiveSize} txs → ${this.config.targetAddress}`,
    );

    // Use local nonce tracking (prevents race conditions on rapid sequential bulks)
    if (this.localNonce === -1) {
      const account = await this.provider.getAccount(senderForProvider as {bech32: () => string});
      this.localNonce = account.nonce;
      log.info(`🔔 First-time nonce fetch: ${this.localNonce}`);
    }
    let currentNonce = this.localNonce;

    const gasPrice = Math.floor(
      this.config.gasPriceBase * this.config.gasPriceMultiplier,
    );
    const estimatedFinalizationMs = estimateFinalizationMs(
      gasPrice,
      this.config.gasPriceBase,
    );

    log.info(
      `Gas price: ${gasPrice} (${this.config.gasPriceMultiplier}x) | Est. finalization: ${estimatedFinalizationMs}ms`,
    );

    const records: TxRecord[] = [];
    const txHashes: string[] = [];
    const failedHashes: string[] = [];
    let retried = 0;

    // Build all transactions first (avoids nonce drift)
    const txBatch = this.buildTransactions(currentNonce, gasPrice, effectiveSize);

    // Send in parallel with concurrency limit
    const queue = [...txBatch];
    const inFlight: Promise<void>[] = [];

    while (queue.length > 0 || inFlight.length > 0) {
      // Check abort
      if (abortSignal?.aborted || this.aborted) {
        log.warn('Bulk send aborted by signal');
        break;
      }

      // Fill up to maxParallelTxs
      while (
        queue.length > 0 &&
        inFlight.length < this.config.maxParallelTxs &&
        !(abortSignal?.aborted || this.aborted)
      ) {
        const tx = queue.shift()!;
        const record: TxRecord = {
          nonce: Number(tx.nonce),
          hash: '',
          status: 'pending',
          attempts: 0,
        };
        records.push(record);

        const taskPromise = this.sendWithRetry(tx, record, abortSignal)
            .then(hash => {
              record.hash = hash;
              record.status = 'success';
              txHashes.push(hash);
              if (record.attempts > 1) retried++;
              log.debug(`✓ TX nonce=${record.nonce} hash=${hash.slice(0, 16)}…`);
            })
            .catch(err => {
              record.status = 'failed';
              record.lastError = (err as Error).message;
              if (record.hash) failedHashes.push(record.hash);
              log.error(
                `✗ TX nonce=${record.nonce} failed: ${record.lastError}`,
              );
            })
            .finally(() => {
              const pos = inFlight.indexOf(taskPromise);
              if (pos !== -1) inFlight.splice(pos, 1);
            });
        inFlight.push(taskPromise);

        // Rate-limit: wait txIntervalMs between dispatches
        if (this.config.txIntervalMs > 0) {
          await sleep(this.config.txIntervalMs);
        }
      }

      if (inFlight.length > 0) {
        // Wait for at least one to finish
        await Promise.race(inFlight);
      }
    }

    // Wait for remaining in-flight
    await Promise.allSettled(inFlight);

    // Safety: if aborted mid-bulk, re-sync nonce from blockchain
    if (abortSignal?.aborted && records.length < this.config.bulkSize) {
      log.warn(
        `⚠️ Bulk aborted after ${records.length}/${this.config.bulkSize} txs — re-syncing nonce from blockchain`,
      );
      await this.initializeNonce();
    }

    const durationMs = Date.now() - start;
    const confirmed = records.filter(r => r.status === 'success').length;
    const failed = records.filter(r => r.status === 'failed').length;

    // Update local nonce for next bulk (critical for continuous sends)
    // IMPORTANT: Increment by actual txs sent, NOT bulkSize (fixes nonce desync on partial sends)
    this.localNonce += records.length;
    log.info(
      `📍 Nonce updated: sent ${records.length}/${this.config.bulkSize} txs, next nonce = ${this.localNonce}`,
    );

    const result: BulkResult = {
      sent: records.length,
      confirmed,
      failed,
      retried,
      txHashes,
      failedHashes,
      durationMs,
      estimatedFinalizationMs,
    };

    log.info(
      `Bulk complete: ${confirmed}/${records.length} sent | ${failed} failed | ${retried} retried | ${durationMs}ms | Next nonce: ${this.localNonce}`,
    );

    return result;
  }

  /**
   * Immediately abort any in-progress bulk send.
   */
  abort(): void {
    this.aborted = true;
    this.abortController?.abort();
    log.info('BulkSender aborted');
  }

  // ── Transaction Building ───────────────────────────────────────────────────

  private buildTransactions(startNonce: number, gasPrice: number, size = this.config.bulkSize): Transaction[] {
    const txs: Transaction[] = [];
    const receiver = Address.newFromBech32(this.config.targetAddress);
    const senderAddr = Address.newFromBech32(this.signer.getAddress().bech32());
    const dataBytes = this.config.txData
      ? Buffer.from(this.config.txData)
      : undefined;

    for (let i = 0; i < size; i++) {
      const tx = new Transaction({
        nonce: BigInt(startNonce + i),
        value: BigInt(this.config.txValue || '0'),
        receiver,
        sender: senderAddr,
        gasPrice: BigInt(gasPrice),
        gasLimit: BigInt(this.config.gasLimitPerTx),
        data: dataBytes,
        chainID: this.config.chainId,
        version: 1,
      });
      txs.push(tx);
    }

    return txs;
  }

  // ── Send with Retry ────────────────────────────────────────────────────────

  private async sendWithRetry(
    tx: Transaction,
    record: TxRecord,
    abortSignal?: AbortSignal,
  ): Promise<string> {
    let lastError: Error | null = null;

    for (let attempt = 1; attempt <= this.config.maxRetryAttempts; attempt++) {
      if (abortSignal?.aborted || this.aborted) {
        throw new Error('Aborted');
      }

      try {
        record.attempts = attempt;

        // Sign transaction (sdk-core v15 pattern)
        const bytesToSign = this.computer.computeBytesForSigning(tx);
        tx.signature = await this.signer.sign(bytesToSign);

        // Send transaction
        const txHash = await this.provider.sendTransaction(tx);
        return txHash;
      } catch (err) {
        lastError = err as Error;
        log.warn(
          `TX nonce=${record.nonce} attempt ${attempt}/${this.config.maxRetryAttempts} failed: ${lastError.message}`,
        );

        // Nonce too high — re-sync immediately and abort retries
        if (lastError.message.includes('veryHighNonceInTx')) {
          log.warn('⚠️ Nonce desync detected (veryHighNonceInTx) — re-syncing from blockchain');
          await this.initializeNonce();
          throw lastError; // Abort this TX; next bulk will use correct nonce
        }

        if (attempt < this.config.maxRetryAttempts) {
          const delay = this.config.retryDelayMs * Math.pow(2, attempt - 1); // Exponential backoff
          await sleep(Math.min(delay, 10_000));
        }
      }
    }

    throw lastError || new Error('Max retry attempts exceeded');
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ─── Factory ─────────────────────────────────────────────────────────────────

export function createBulkSender(
  config: BulkConfig,
  signer: UserSigner,
): BulkSender {
  return new BulkSender(config, signer);
}
