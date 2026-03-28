#!/usr/bin/env ts-node

/**
 * generate-wallets.ts — Simplified
 *
 * Generates N wallets for Challenge 5.
 * Each wallet is ready to be used via the orchestrator.
 */

import * as fs from 'fs';
import * as path from 'path';
import {randomBytes} from 'crypto';
import {UserSigner} from '@multiversx/sdk-wallet';

interface GenerateOptions {
  count: number;
  outputDir: string;
  dryRun: boolean;
}

function parseArgs(): GenerateOptions {
  const args = process.argv.slice(2);
  const options: GenerateOptions = {
    count: 10,
    outputDir: './wallets',
    dryRun: false,
  };

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--count') options.count = parseInt(args[++i], 10);
    if (args[i] === '--output') options.outputDir = args[++i];
    if (args[i] === '--dry-run') options.dryRun = true;
  }

  return options;
}

async function generateWallets(opts: GenerateOptions): Promise<void> {
  console.log(`\n${'='.repeat(60)}`);
  console.log(`Challenge 5 — Multi-Agent Wallet Generator`);
  console.log(`${'='.repeat(60)}\n`);

  console.log(`🔑 Generating ${opts.count} wallets…`);
  if (opts.dryRun) console.log(`   (DRY RUN — no files written)\n`);

  // Ensure output directory exists
  if (!opts.dryRun) {
    fs.mkdirSync(opts.outputDir, {recursive: true});
  }

  const wallets: Array<{index: number; address: string; pemPath: string}> = [];

  for (let i = 0; i < opts.count; i++) {
    try {
      // Generate a new random 64-byte private key (MultiversX expects 64 bytes)
      const privateKeyBytes = randomBytes(64);
      // MultiversX format: hex(private_key) as base64
      const hexPrivateKey = privateKeyBytes.toString('hex');
      const base64PrivateKey = Buffer.from(hexPrivateKey).toString('base64');

      if (!opts.dryRun) {
        const pemPath = path.join(opts.outputDir, `agent_${i.toString().padStart(2, '0')}.pem`);

        try {
          // Create temporary PEM to get the address
          const tempPem = `-----BEGIN PRIVATE KEY-----\n${base64PrivateKey}\n-----END PRIVATE KEY-----`;
          const signer = UserSigner.fromPem(tempPem);
          const address = signer.getAddress().bech32();

          // Create PEM format with address in header (like the existing wallet.pem)
          const pemContent = `-----BEGIN PRIVATE KEY for ${address}-----\n${base64PrivateKey}\n-----END PRIVATE KEY for ${address}-----`;

          // Write PEM file
          fs.writeFileSync(pemPath, pemContent, 'utf-8');

          console.log(`✅ Wallet ${i.toString().padStart(2, '0')}: ${address}`);

          wallets.push({
            index: i,
            address,
            pemPath,
          });
        } catch (signerErr) {
          console.error(`❌ Error creating wallet ${i}: ${(signerErr as Error).message}`);
          // Still add to wallets for reference
          wallets.push({
            index: i,
            address: `erd1unknown`,
            pemPath,
          });
        }
      } else {
        const mockAddress = `erd1agent${i.toString().padStart(57, '0')}`;
        console.log(`(DRY) Wallet ${i.toString().padStart(2, '0')}: ${mockAddress}`);
        wallets.push({
          index: i,
          address: mockAddress,
          pemPath: path.join(opts.outputDir, `agent_${i.toString().padStart(2, '0')}.pem`),
        });
      }
    } catch (err) {
      console.error(`❌ Error generating wallet ${i}: ${(err as Error).message}`);
    }
  }

  // Write summary file
  if (!opts.dryRun) {
    const summaryPath = path.join(opts.outputDir, 'wallets.json');
    const summary = {
      generated: new Date().toISOString(),
      count: opts.count,
      totalFundingRequired: `${opts.count * 50} EGLD`,
      perWalletFunding: '50 EGLD',
      wallets: wallets.map((w) => ({
        index: w.index,
        address: w.address,
        pemFile: path.basename(w.pemPath),
      })),
    };
    fs.writeFileSync(summaryPath, JSON.stringify(summary, null, 2), 'utf-8');
    console.log(`\n📄 Summary: ${summaryPath}`);
  }

  console.log(`\n${'─'.repeat(60)}`);
  console.log(`Next Steps:`);
  console.log(`  1. Verify wallets are in ${opts.outputDir}/`);
  console.log(`  2. Fund each wallet with 50 EGLD`);
  console.log(`  3. Run: npm run challenge5:register-wallets`);
  console.log(`  4. Launch: npm run challenge5:multi -- --agents ${opts.count}`);
  console.log(`\n${'='.repeat(60)}\n`);
}

generateWallets(parseArgs()).catch((err) => {
  console.error('❌ Error:', err.message);
  process.exit(1);
});
