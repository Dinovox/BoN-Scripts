/**
 * Challenge 5 — Semantic Interpreter
 *
 * NLP layer for interpreting natural language commands received on-chain.
 * Uses regex patterns + weighted scoring with adversarial input detection.
 * Enhanced with LLM fallback via OpenRouter for ambiguous/adversarial commands.
 *
 * Returns: { intent: 'GREEN' | 'RED' | 'UNKNOWN', confidence: 0–1, reasoning: string }
 *
 * Design notes:
 * - interpret()      : synchronous regex-only (backward-compatible, used by tests)
 * - interpretAsync() : hybrid — regex fast-path (conf ≥ 0.85) → LLM for ambiguous cases
 * - LLM key loaded automatically from OPENROUTER_API_KEY env or openclaw auth profiles
 * - LLM timeout: 3s — falls back to regex result on failure
 */

import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

export type Intent = 'GREEN' | 'RED' | 'UNKNOWN';

export interface InterpretResult {
  intent: Intent;
  confidence: number; // 0.0 – 1.0
  reasoning: string;
  raw: string;
  adversarial: boolean;
}

// ─── Signal Definitions ──────────────────────────────────────────────────────

interface SignalRule {
  pattern: RegExp;
  score: number; // positive = GREEN, negative = RED
  label: string;
}

const GREEN_SIGNALS: SignalRule[] = [
  {pattern: /\bgreen\b/i, score: 1.0, label: 'keyword:green'},
  {pattern: /\bstart\b/i, score: 0.7, label: 'keyword:start'},
  {pattern: /\bgo\b/i, score: 0.5, label: 'keyword:go'},
  {pattern: /\bbegin\b/i, score: 0.6, label: 'keyword:begin'},
  {pattern: /\bsend\b/i, score: 0.5, label: 'keyword:send'},
  {pattern: /\bfire\b/i, score: 0.5, label: 'keyword:fire'},
  {pattern: /\blaunch\b/i, score: 0.6, label: 'keyword:launch'},
  {pattern: /\bactivate\b/i, score: 0.6, label: 'keyword:activate'},
  {pattern: /\benable\b/i, score: 0.5, label: 'keyword:enable'},
  {pattern: /\brun\b/i, score: 0.4, label: 'keyword:run'},
  {pattern: /\bproceed\b/i, score: 0.6, label: 'keyword:proceed'},
  {pattern: /\bexecute\b/i, score: 0.6, label: 'keyword:execute'},
  {pattern: /\byes\b/i, score: 0.4, label: 'keyword:yes'},
  {pattern: /\bgo\s+ahead\b/i, score: 0.7, label: 'phrase:go-ahead'},
  {pattern: /\bfull\s+speed\b/i, score: 0.8, label: 'phrase:full-speed'},
  {
    pattern: /\blet['']?s?\s+(go|start|roll)\b/i,
    score: 0.7,
    label: 'phrase:lets-go',
  },
  {pattern: /\btime\s+to\s+(go|start|send)\b/i, score: 0.7, label: 'phrase:time-to'},
  {pattern: /💚|🟢|✅/u, score: 0.8, label: 'emoji:green'},
];

const RED_SIGNALS: SignalRule[] = [
  {pattern: /\bred\b/i, score: 1.0, label: 'keyword:red'},
  {pattern: /\bstop\b/i, score: 0.8, label: 'keyword:stop'},
  {pattern: /\bhalt\b/i, score: 0.8, label: 'keyword:halt'},
  {pattern: /\bpause\b/i, score: 0.7, label: 'keyword:pause'},
  {pattern: /\bcancel\b/i, score: 0.7, label: 'keyword:cancel'},
  {pattern: /\babort\b/i, score: 0.8, label: 'keyword:abort'},
  {pattern: /\bkill\b/i, score: 0.7, label: 'keyword:kill'},
  {pattern: /\bend\b/i, score: 0.4, label: 'keyword:end'},
  {pattern: /\bfreeze\b/i, score: 0.7, label: 'keyword:freeze'},
  {pattern: /\bno\b/i, score: 0.3, label: 'keyword:no'},
  {pattern: /\bdon['']?t\b/i, score: 0.4, label: 'keyword:dont'},
  {pattern: /\bterminate\b/i, score: 0.7, label: 'keyword:terminate'},
  {pattern: /\bshutdown\b/i, score: 0.7, label: 'keyword:shutdown'},
  {pattern: /\bstand\s+down\b/i, score: 0.8, label: 'phrase:stand-down'},
  {pattern: /\bceasefire\b/i, score: 0.9, label: 'phrase:ceasefire'},
  {pattern: /\bbrake\b/i, score: 0.6, label: 'keyword:brake'},
  {pattern: /❌|🛑|🔴|⛔/u, score: 0.8, label: 'emoji:red'},
];

// ─── Adversarial Detection Patterns ──────────────────────────────────────────

const ADVERSARIAL_PATTERNS: Array<{pattern: RegExp; label: string}> = [
  // Double negations: "don't stop" (actually GREEN, but suspicious)
  {pattern: /\bdon['']?t\s+stop\b/i, label: 'double-negation:dont-stop'},
  {pattern: /\bnot\s+red\b/i, label: 'negation:not-red'},
  {pattern: /\bnot\s+green\b/i, label: 'negation:not-green'},
  {pattern: /\bno\s+stop\b/i, label: 'negation:no-stop'},
  // Obfuscation / l33tspeak
  {pattern: /\bgr[3e][e3]n\b/i, label: 'leet:green'},
  {pattern: /\br[e3]d\b/i, label: 'leet:red'},
  // Unicode homoglyphs
  {pattern: /[ɡɢ][rʀ][eɛ][eɛ][nɴ]/i, label: 'homoglyph:green'},
  // Mixed-case obfuscation via alternating caps (GrEeN, rEd, etc.)
  {
    pattern: /[Gg][Rr][Ee][Ee][Nn]|[Rr][Ee][Dd]/,
    label: 'mixedcase',
  },
  // Contradiction: both keywords in short text
  {pattern: /\bgreen\b.*\bred\b|\bred\b.*\bgreen\b/i, label: 'contradiction:both'},
  // Negated command: "do not send", "please no"
  {
    pattern: /\b(do\s+not|please\s+no|never\s+start|don['']?t\s+go)\b/i,
    label: 'negated-command',
  },
  // Markdown/code formatting injections
  {pattern: /`[^`]*`|\*\*[^*]*\*\*/, label: 'formatting-injection'},
];

// ─── Negation Window ─────────────────────────────────────────────────────────

/**
 * Checks if a word at `matchIndex` is immediately preceded by a negation.
 * Window: 3 words before the match.
 */
function isNegated(text: string, matchIndex: number): boolean {
  const before = text.slice(Math.max(0, matchIndex - 30), matchIndex);
  return /\b(not|no|never|don['']?t|cannot|can['']?t|stop)\s*$/i.test(before);
}

// ─── Core Interpreter ────────────────────────────────────────────────────────

export class SemanticInterpreter {
  private readonly confidenceThreshold: number;
  private readonly adversarialPenalty: number;
  private readonly adversarialDetection: boolean;

  constructor(
    confidenceThreshold = 0.6,
    adversarialPenalty = 0.3,
    adversarialDetection = true,
  ) {
    this.confidenceThreshold = confidenceThreshold;
    this.adversarialPenalty = adversarialPenalty;
    this.adversarialDetection = adversarialDetection;
  }

  /**
   * Main interpretation method.
   * Decodes base64 data field if needed, then scores signals.
   */
  interpret(rawData: string): InterpretResult {
    const text = this.decodeData(rawData);
    const normalized = text.trim();

    // ── Adversarial checks ────────────────────────────────────────────────
    const adversarialFlags: string[] = [];
    if (this.adversarialDetection) {
      for (const ap of ADVERSARIAL_PATTERNS) {
        if (ap.pattern.test(normalized)) {
          adversarialFlags.push(ap.label);
        }
      }
    }
    const isAdversarial = adversarialFlags.length > 0;

    // ── Score GREEN signals ───────────────────────────────────────────────
    let greenScore = 0;
    const greenHits: string[] = [];
    for (const sig of GREEN_SIGNALS) {
      const match = sig.pattern.exec(normalized);
      if (match) {
        if (!isNegated(normalized, match.index)) {
          greenScore += sig.score;
          greenHits.push(sig.label);
        } else {
          // Negated green signal → mild red push
          greenScore -= sig.score * 0.5;
          greenHits.push(`[negated]${sig.label}`);
        }
      }
    }

    // ── Score RED signals ─────────────────────────────────────────────────
    let redScore = 0;
    const redHits: string[] = [];
    for (const sig of RED_SIGNALS) {
      const match = sig.pattern.exec(normalized);
      if (match) {
        if (!isNegated(normalized, match.index)) {
          redScore += sig.score;
          redHits.push(sig.label);
        } else {
          // Negated red signal → mild green push
          redScore -= sig.score * 0.5;
          redHits.push(`[negated]${sig.label}`);
        }
      }
    }

    // ── Apply adversarial penalty ─────────────────────────────────────────
    if (isAdversarial) {
      greenScore -= this.adversarialPenalty;
      redScore -= this.adversarialPenalty;
    }

    // ── Determine intent ──────────────────────────────────────────────────
    let intent: Intent = 'UNKNOWN';
    let confidence = 0;
    let reasoning = '';

    if (greenScore <= 0 && redScore <= 0) {
      intent = 'UNKNOWN';
      confidence = 0;
      reasoning = `No clear signal detected. Text: "${normalized.slice(0, 100)}"`;
    } else if (greenScore > redScore) {
      // Epsilon 0.1 avoids over-penalizing strong single-keyword signals
      const opposition = Math.max(redScore, 0);
      const rawConf = greenScore / (greenScore + opposition + 0.1);
      confidence = Math.min(rawConf, 1.0);
      intent = confidence >= this.confidenceThreshold ? 'GREEN' : 'UNKNOWN';
      reasoning = `GREEN signals (${greenScore.toFixed(2)}): [${greenHits.join(', ')}]`;
      if (redHits.length)
        reasoning += ` | RED noise (${redScore.toFixed(2)}): [${redHits.join(', ')}]`;
    } else {
      const opposition = Math.max(greenScore, 0);
      const rawConf = redScore / (redScore + opposition + 0.1);
      confidence = Math.min(rawConf, 1.0);
      intent = confidence >= this.confidenceThreshold ? 'RED' : 'UNKNOWN';
      reasoning = `RED signals (${redScore.toFixed(2)}): [${redHits.join(', ')}]`;
      if (greenHits.length)
        reasoning += ` | GREEN noise (${greenScore.toFixed(2)}): [${greenHits.join(', ')}]`;
    }

    if (isAdversarial) {
      reasoning += ` ⚠️ ADVERSARIAL flags: [${adversarialFlags.join(', ')}] — confidence penalized by ${this.adversarialPenalty}`;
      // Force UNKNOWN if adversarial and scores are close
      if (Math.abs(greenScore - redScore) < 0.5) {
        intent = 'UNKNOWN';
        reasoning += ' → forced UNKNOWN (scores too close after penalty)';
      }
    }

    return {
      intent,
      confidence: Math.max(0, Math.round(confidence * 1000) / 1000),
      reasoning,
      raw: normalized,
      adversarial: isAdversarial,
    };
  }

  /**
   * Hybrid interpretation: regex fast-path (conf ≥ 0.85 and not adversarial)
   * → LLM via OpenRouter for ambiguous/adversarial commands.
   * Falls back to regex result if LLM is unavailable or times out (3s).
   */
  async interpretAsync(rawData: string): Promise<InterpretResult> {
    const syncResult = this.interpret(rawData);

    // Fast path: high-confidence, non-adversarial → skip LLM
    if (syncResult.confidence >= 0.85 && !syncResult.adversarial) {
      return syncResult;
    }

    const apiKey = loadOpenRouterKey();
    if (!apiKey) {
      return syncResult; // No key configured → regex only
    }

    try {
      const llmIntent = await callOpenRouterClassifier(rawData, apiKey);
      return {
        intent: llmIntent,
        confidence: 0.95,
        reasoning: `LLM(openrouter) → ${llmIntent} [regex was: ${syncResult.intent} @ ${syncResult.confidence}]`,
        raw: syncResult.raw,
        adversarial: syncResult.adversarial,
      };
    } catch {
      return syncResult; // LLM failed/timed out → fall back to regex
    }
  }

  /**
   * Attempts to decode base64-encoded data field (MultiversX on-chain data).
   * Falls back to raw string if not valid base64.
   */
  private decodeData(raw: string): string {
    // MultiversX data fields are hex or base64; try both
    try {
      const buf = Buffer.from(raw, 'base64');
      const decoded = buf.toString('utf8');
      // Sanity: decoded must be printable ASCII/UTF-8
      if (/^[\x20-\x7E\u00C0-\u024F\u0400-\u04FF\s]*$/.test(decoded)) {
        return decoded;
      }
    } catch {}
    try {
      // Try hex decode
      if (/^[0-9a-fA-F]+$/.test(raw) && raw.length % 2 === 0) {
        return Buffer.from(raw, 'hex').toString('utf8');
      }
    } catch {}
    return raw;
  }
}

// ─── Singleton Export ─────────────────────────────────────────────────────────

let _interpreter: SemanticInterpreter | null = null;

export function getInterpreter(
  confidenceThreshold?: number,
  adversarialPenalty?: number,
  adversarialDetection?: boolean,
): SemanticInterpreter {
  if (!_interpreter) {
    _interpreter = new SemanticInterpreter(
      confidenceThreshold,
      adversarialPenalty,
      adversarialDetection,
    );
  }
  return _interpreter;
}

// ─── OpenRouter LLM Helpers ───────────────────────────────────────────────────

/**
 * Loads the OpenRouter API key from env or openclaw auth profiles.
 */
function loadOpenRouterKey(): string | null {
  if (process.env.OPENROUTER_API_KEY) return process.env.OPENROUTER_API_KEY;
  try {
    const profilesPath = path.join(
      os.homedir(),
      '.openclaw',
      'agents',
      'main',
      'agent',
      'auth-profiles.json',
    );
    const raw = fs.readFileSync(profilesPath, 'utf8');
    const data = JSON.parse(raw) as {
      profiles?: Record<string, {key?: string}>;
    };
    return data?.profiles?.['openrouter:default']?.key ?? null;
  } catch {
    return null;
  }
}

/**
 * Calls OpenRouter chat completions to classify a command as GREEN or RED.
 * Uses claude-haiku-4-5 for speed. Timeout: 3 seconds.
 */
async function callOpenRouterClassifier(
  text: string,
  apiKey: string,
): Promise<Intent> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3000);

  try {
    const resp = await fetch('https://openrouter.ai/api/v1/chat/completions', {
      method: 'POST',
      signal: controller.signal,
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: 'anthropic/claude-haiku-4-5',
        max_tokens: 5,
        temperature: 0,
        messages: [
          {
            role: 'system',
            content:
              'You classify admin commands for a blockchain bot. ' +
              'GREEN = proceed / go / move / start / open / active / forward. ' +
              'RED = stop / pause / wait / halt / freeze / danger / back. ' +
              'Commands use poetic/metaphorical language. Examples:\n' +
              'GREEN: "step inside", "move forward", "doors open", "breathe and move", "proceed", "launch", "activate"\n' +
              'RED: "stand still", "wait here", "freeze", "pause", "hold position", "retreat", "danger"\n' +
              'Reply with exactly one word: GREEN or RED.',
          },
          {
            role: 'user',
            content: `Command: "${text.slice(0, 200)}"`,
          },
        ],
      }),
    });

    if (!resp.ok) throw new Error(`OpenRouter ${resp.status}`);

    const data = (await resp.json()) as {
      choices?: Array<{message?: {content?: string}}>;
    };
    const answer = data?.choices?.[0]?.message?.content?.trim().toUpperCase() ?? '';

    if (answer.includes('GREEN')) return 'GREEN';
    if (answer.includes('RED')) return 'RED';
    return 'RED'; // Conservative default
  } finally {
    clearTimeout(timeout);
  }
}
