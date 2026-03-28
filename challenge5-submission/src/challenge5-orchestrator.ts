#!/usr/bin/env ts-node

/**
 * challenge5-orchestrator.ts
 *
 * Orchestrates N independent Challenge 5 agents, each with its own wallet.
 * Coordinates command distribution and aggregates scores.
 *
 * Usage:
 *   npx ts-node src/challenge5-orchestrator.ts --agents 10 --wallets-dir ./wallets
 *   npm run challenge5:multi -- --agents 10
 */

import * as fs from 'fs';
import * as path from 'path';
import {spawn, ChildProcess} from 'child_process';
import {Logger, LogLevel} from './utils/logger';

const log = new Logger('Challenge5:Orchestrator', LogLevel.INFO);

interface Agent {
  index: number;
  walletPath: string;
  address: string;
  process: ChildProcess | null;
  bulkCount: number;
  txCount: number;
  score: number;
}

interface OrchestratorOptions {
  agentCount: number;
  walletsDir: string;
  adminAddress: string;
  targetAddress: string;
  logDir: string;
}

class Challenge5Orchestrator {
  private agents: Agent[] = [];
  private options: OrchestratorOptions;
  private running = false;
  private logStream: fs.WriteStream | null = null;

  constructor(options: OrchestratorOptions) {
    this.options = options;
  }

  async start(): Promise<void> {
    if (this.running) {
      log.warn('Orchestrator already running');
      return;
    }

    console.log(`\n${'='.repeat(70)}`);
    console.log(`🤖 Challenge 5 — Multi-Agent Orchestrator`);
    console.log(`${'='.repeat(70)}\n`);

    console.log(`📊 Configuration:`);
    console.log(`   Agents: ${this.options.agentCount}`);
    console.log(`   Wallets dir: ${this.options.walletsDir}`);
    console.log(`   Admin wallet: ${this.options.adminAddress.slice(0, 16)}…`);
    console.log(`   Target wallet: ${this.options.targetAddress.slice(0, 16)}…\n`);

    // Initialize log stream
    const logPath = path.join(this.options.logDir, `orchestrator-${new Date().toISOString().split('T')[0]}.log`);
    fs.mkdirSync(this.options.logDir, {recursive: true});
    this.logStream = fs.createWriteStream(logPath, {flags: 'a'});

    // Load wallets
    await this.loadWallets();

    // Spawn agents
    this.running = true;
    for (const agent of this.agents) {
      await this.spawnAgent(agent);
    }

    console.log(`\n${'─'.repeat(70)}`);
    console.log(`✅ All ${this.agents.length} agents launched\n`);

    // Monitor agents
    this.monitorAgents();

    // Graceful shutdown
    process.on('SIGINT', () => this.shutdown('SIGINT'));
    process.on('SIGTERM', () => this.shutdown('SIGTERM'));
  }

  private async loadWallets(): Promise<void> {
    console.log(`📂 Loading wallets from ${this.options.walletsDir}…`);

    const walletFiles = fs
      .readdirSync(this.options.walletsDir)
      .filter((f) => f.startsWith('agent_') && f.endsWith('.pem'))
      .sort()
      .slice(0, this.options.agentCount);

    for (let i = 0; i < walletFiles.length; i++) {
      const walletFile = walletFiles[i];
      const walletPath = path.join(this.options.walletsDir, walletFile);
      const walletData = fs.readFileSync(walletPath, 'utf-8');
      const addressMatch = walletData.match(/Address: (erd1[a-z0-9]+)/);
      const address = addressMatch ? addressMatch[1] : `unknown_${i}`;

      this.agents.push({
        index: i,
        walletPath,
        address,
        process: null,
        bulkCount: 0,
        txCount: 0,
        score: 0,
      });

      console.log(`   ${i.toString().padStart(2, '0')}: ${address}`);
    }

    console.log(`✅ Loaded ${this.agents.length} wallets\n`);
  }

  private async spawnAgent(agent: Agent): Promise<void> {
    log.info(`🚀 Spawning agent ${agent.index} (${agent.address.slice(0, 16)}…)`);

    // Build environment for this agent
    const env = {
      ...process.env,
      MULTIVERSX_PRIVATE_KEY: agent.walletPath,
      AGENT_INDEX: agent.index.toString(),
      AGENT_ADDRESS: agent.address,
      ADMIN_WALLET_ADDRESS: this.options.adminAddress,
      TARGET_WALLET_ADDRESS: this.options.targetAddress,
    };

    // Spawn child process running challenge5:start with this wallet
    const agentProcess = spawn('npm', ['run', 'challenge5:start'], {
      cwd: process.cwd(),
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    agent.process = agentProcess;

    // Capture agent logs
    const agentLogPath = path.join(this.options.logDir, `agent_${agent.index.toString().padStart(2, '0')}.log`);
    const agentLogStream = fs.createWriteStream(agentLogPath, {flags: 'a'});

    agentProcess.stdout?.pipe(agentLogStream);
    agentProcess.stderr?.pipe(agentLogStream);

    // Monitor for exit
    agentProcess.on('exit', (code) => {
      log.warn(`Agent ${agent.index} exited with code ${code}`);
      if (this.running) {
        // Optionally respawn, but for now just log
        log.error(`Agent ${agent.index} died — will not auto-respawn`);
      }
    });

    // Stagger agent starts to avoid nonce race conditions
    const delayMs = agent.index * 100; // 0ms, 100ms, 200ms, ...
    await this.sleep(delayMs);
  }

  private monitorAgents(): void {
    const monitorInterval = setInterval(() => {
      const runningCount = this.agents.filter((a) => a.process && !a.process.killed).length;
      const totalScore = this.agents.reduce((sum, a) => sum + a.score, 0);

      process.stdout.write(
        `\r📊 Agents: ${runningCount}/${this.agents.length} running | Total score: ${totalScore} | ` +
          `Time: ${new Date().toISOString().split('T')[1]}`,
      );
    }, 1000);

    // Stop monitoring on shutdown
    process.on('exit', () => clearInterval(monitorInterval));
  }

  private async shutdown(reason: string): Promise<void> {
    console.log(`\n\n${'─'.repeat(70)}`);
    console.log(`🛑 Shutting down (${reason})…\n`);

    this.running = false;

    // Kill all child processes
    for (const agent of this.agents) {
      if (agent.process && !agent.process.killed) {
        log.info(`Terminating agent ${agent.index}`);
        agent.process.kill('SIGTERM');
      }
    }

    // Wait for processes to exit
    await this.sleep(2000);

    // Force kill any remaining
    for (const agent of this.agents) {
      if (agent.process && !agent.process.killed) {
        agent.process.kill('SIGKILL');
      }
    }

    // Close logs
    this.logStream?.end();

    console.log(`✅ Orchestrator shutdown complete\n`);
    process.exit(0);
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

// ─── Main ─────────────────────────────────────────────────────────────────────

function parseArgs(): OrchestratorOptions {
  const args = process.argv.slice(2);
  const options: OrchestratorOptions = {
    agentCount: 10,
    walletsDir: './wallets',
    adminAddress: process.env.ADMIN_WALLET_ADDRESS || '',
    targetAddress: process.env.TARGET_WALLET_ADDRESS || '',
    logDir: './logs',
  };

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--agents') options.agentCount = parseInt(args[++i], 10);
    if (args[i] === '--wallets-dir') options.walletsDir = args[++i];
    if (args[i] === '--admin') options.adminAddress = args[++i];
    if (args[i] === '--target') options.targetAddress = args[++i];
    if (args[i] === '--logs') options.logDir = args[++i];
  }

  if (!options.adminAddress || !options.targetAddress) {
    console.error('❌ Missing ADMIN_WALLET_ADDRESS or TARGET_WALLET_ADDRESS');
    console.error('   Set via env vars or --admin / --target flags');
    process.exit(1);
  }

  return options;
}

const options = parseArgs();
const orchestrator = new Challenge5Orchestrator(options);
orchestrator.start().catch((err) => {
  console.error('❌ Orchestrator error:', err.message);
  process.exit(1);
});
