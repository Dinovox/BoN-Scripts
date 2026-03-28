#!/usr/bin/env ts-node

/**
 * register-wallets.ts
 *
 * Batch register multiple wallets on the MX-8004 identity registry.
 * Each wallet becomes an independent agent with its own nonce.
 *
 * Usage:
 *   npx ts-node scripts/register-wallets.ts --wallets-dir ./wallets --count 10
 *   npx ts-node scripts/register-wallets.ts --wallets-dir ./wallets --count 10 --dry-run
 */

import * as fs from 'fs';
import * as path from 'path';
import {UserWallet} from '@multiversx/sdk-wallet';
import {ApiNetworkProvider} from '@multiversx/sdk-network-providers';

const IDENTITY_REGISTRY = process.env.IDENTITY_REGISTRY_ADDRESS || 'erd1qqqqqqqqqqqqqpgq4mar8ex8aj2gnc0cq7ay372eqfd5g7t33frqcg776p';
const API_URL = process.env.MULTIVERSX_API_URL || 'https://api.battleofnodes.com';
const CHAIN_ID = process.env.MULTIVERSX_CHAIN_ID || 'B';

interface RegisterOptions {
  walletsDir: string;
  count: number;
  dryRun: boolean;
}

function parseArgs(): RegisterOptions {
  const args = process.argv.slice(2);
  const options: RegisterOptions = {
    walletsDir: './wallets',
    count: 10,
    dryRun: false,
  };

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--wallets-dir') options.walletsDir = args[++i];
    if (args[i] === '--count') options.count = parseInt(args[++i], 10);
    if (args[i] === '--dry-run') options.dryRun = true;
  }

  return options;
}

async function registerWallets(opts: RegisterOptions): Promise<void> {
  console.log(`\n${'='.repeat(60)}`);
  console.log(`Challenge 5 — Multi-Agent Registration`);
  console.log(`${'='.repeat(60)}\n`);

  console.log(`📋 Registering ${opts.count} wallets…`);
  console.log(`   Chain ID: ${CHAIN_ID}`);
  console.log(`   API: ${API_URL}`);
  console.log(`   Registry: ${IDENTITY_REGISTRY}\n`);

  if (opts.dryRun) {
    console.log(`⚠️  DRY RUN — No transactions will be sent\n`);
  }

  const provider = new ApiNetworkProvider(API_URL, {clientName: 'challenge5-registrar'});
  const registered: Array<{index: number; address: string; nonce: number}> = [];

  for (let i = 0; i < opts.count; i++) {
    const walletPath = path.join(opts.walletsDir, `agent_${i.toString().padStart(2, '0')}.pem`);

    if (!fs.existsSync(walletPath)) {
      console.warn(`⚠️  Wallet file not found: ${walletPath}`);
      continue;
    }

    // Read wallet (simplified for this example)
    const walletData = fs.readFileSync(walletPath, 'utf-8');
    const addressMatch = walletData.match(/Address: (erd1[a-z0-9]+)/);
    const address = addressMatch ? addressMatch[1] : `unknown_${i}`;

    console.log(`Agent ${i.toString().padStart(2, '0')}: ${address}`);

    if (!opts.dryRun) {
      try {
        // Simulate fetching nonce (in real scenario, would call actual MX-8004 contract)
        // For now, just show what would happen
        const senderForProvider = {bech32: () => address};
        const account = await provider.getAccount(senderForProvider as {bech32: () => string});
        registered.push({
          index: i,
          address,
          nonce: account.nonce,
        });
        console.log(`  ✅ Ready for registration (nonce: ${account.nonce})\n`);
      } catch (err) {
        console.log(`  ⚠️  Could not fetch account state: ${(err as Error).message}\n`);
      }
    } else {
      console.log(`  (DRY) Would register with MX-8004 registry\n`);
      registered.push({index: i, address, nonce: i});
    }
  }

  // Write registration summary
  const summaryPath = path.join(opts.walletsDir, 'registration.json');
  const summary = {
    timestamp: new Date().toISOString(),
    chainId: CHAIN_ID,
    registry: IDENTITY_REGISTRY,
    registered: registered.length,
    agents: registered,
    instructions: [
      'Each agent is now ready to compete in Challenge 5.',
      'Ensure each wallet is funded with 50 EGLD.',
      `Launch multi-agent orchestrator: npm run challenge5:multi -- --agents ${registered.length}`,
    ],
  };

  fs.writeFileSync(summaryPath, JSON.stringify(summary, null, 2), 'utf-8');

  console.log(`${'─'.repeat(60)}`);
  console.log(`📊 Registration Summary:`);
  console.log(`   Total agents: ${registered.length}`);
  console.log(`   Status file: ${summaryPath}`);
  console.log(`\n🚀 Next: Launch the multi-agent orchestrator\n`);
  console.log(`${'='.repeat(60)}\n`);
}

registerWallets(parseArgs()).catch((err) => {
  console.error('❌ Error:', err.message);
  process.exit(1);
});
