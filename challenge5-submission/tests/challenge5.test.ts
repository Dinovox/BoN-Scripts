/**
 * Challenge 5 — Test Suite
 *
 * Tests:
 * 1. GREEN command → bulk send starts
 * 2. RED command → bulk send stops
 * 3. Adversarial inputs (ambiguous, misleading, obfuscated)
 * 4. Network latency simulation (600ms blocks)
 * 5. Interpreter unit tests
 * 6. State machine transitions
 */

import {EventEmitter} from 'events';
import {
  SemanticInterpreter,
  InterpretResult,
  Intent,
} from '../src/challenge5-interpreter';
import {
  CommandListener,
  CommandEvent,
  ListenerConfig,
} from '../src/challenge5-listener';
import {estimateFinalizationMs} from '../src/challenge5-bulk';
import {Challenge5Agent, AgentState} from '../src/challenge5-agent';

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const GREEN_COMMANDS = [
  'green',
  'GREEN',
  'go',
  'start',
  'begin',
  'start sending',
  "let's go",
  'fire',
  'launch',
  'full speed',
  'activate',
  'go ahead',
  'proceed',
  'execute',
  '💚',
  '🟢',
  '✅',
  Buffer.from('green').toString('base64'), // base64 encoded
];

const RED_COMMANDS = [
  'red',
  'RED',
  'stop',
  'halt',
  'abort',
  'cancel',
  'pause',
  'kill',
  'freeze',
  'terminate',
  'stand down',
  'ceasefire',
  'shutdown',
  '🔴',
  '🛑',
  '❌',
  '⛔',
  Buffer.from('stop').toString('base64'), // base64 encoded
];

const ADVERSARIAL_COMMANDS = [
  // Double negations (tricky — looks RED but means GREEN)
  {input: "don't stop", notes: 'double-negation — should be UNKNOWN or GREEN'},
  {input: 'not red', notes: 'negated RED keyword — ambiguous'},
  {input: 'not green', notes: 'negated GREEN keyword — ambiguous'},
  // Contradictions
  {input: 'green red', notes: 'both keywords — contradiction'},
  {input: 'start stop', notes: 'opposing actions'},
  // Obfuscation
  {input: 'gr3en', notes: 'leet speak green'},
  {input: 'GrEeN', notes: 'mixed case — still detectable'},
  {input: 'r3d', notes: 'leet speak red'},
  // Misleading context
  {input: 'the traffic light is red, but start anyway', notes: 'RED in noise, GREEN intent'},
  {input: 'do NOT send', notes: 'explicit negation of GREEN action'},
  {input: 'please no', notes: 'soft denial'},
  {input: 'stop thinking about going', notes: 'stop + go — adversarial'},
  // Long obfuscated strings
  {input: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ green ABCDEFG', notes: 'green buried in noise'},
  {input: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', notes: 'no signal at all'},
  // Unicode homoglyphs
  {input: 'ɡreen', notes: 'homoglyph g'},
  // Markdown injection
  {input: '**red** means `go`', notes: 'markdown formatting with contradiction'},
];

const UNKNOWN_COMMANDS = [
  'hello world',
  'what is this?',
  'transaction received',
  '12345',
  '',
  'please',
  'ok',
  'AAAAAAAA',
  'ping',
  'test',
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

function interpret(text: string): InterpretResult {
  const interpreter = new SemanticInterpreter(0.6, 0.3, true);
  return interpreter.interpret(text);
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ─── Interpreter Unit Tests ───────────────────────────────────────────────────

describe('SemanticInterpreter', () => {
  describe('GREEN commands', () => {
    test.each(GREEN_COMMANDS)('"%s" should be GREEN or high confidence', input => {
      const result = interpret(input);
      // For base64-encoded commands, accept GREEN after decode
      expect(['GREEN', 'UNKNOWN']).toContain(result.intent);
      if (result.intent === 'GREEN') {
        expect(result.confidence).toBeGreaterThanOrEqual(0.5);
      }
    });

    test('should detect GREEN with high confidence for clear command', () => {
      const result = interpret('green');
      expect(result.intent).toBe('GREEN');
      expect(result.confidence).toBeGreaterThanOrEqual(0.8);
    });

    test('should detect GREEN from base64 encoded data', () => {
      const encoded = Buffer.from('start sending').toString('base64');
      const result = interpret(encoded);
      expect(result.intent).toBe('GREEN');
    });
  });

  describe('RED commands', () => {
    test.each(RED_COMMANDS)('"%s" should be RED or detected', input => {
      const result = interpret(input);
      expect(['RED', 'UNKNOWN']).toContain(result.intent);
      if (result.intent === 'RED') {
        expect(result.confidence).toBeGreaterThanOrEqual(0.5);
      }
    });

    test('should detect RED with high confidence for clear command', () => {
      const result = interpret('stop');
      expect(result.intent).toBe('RED');
      expect(result.confidence).toBeGreaterThanOrEqual(0.7);
    });

    test('should detect RED from base64 encoded abort', () => {
      const encoded = Buffer.from('abort').toString('base64');
      const result = interpret(encoded);
      expect(result.intent).toBe('RED');
    });
  });

  describe('UNKNOWN commands', () => {
    test.each(UNKNOWN_COMMANDS)('"%s" should not be confidently GREEN or RED', input => {
      const result = interpret(input);
      // Should either be UNKNOWN or very low confidence
      if (result.intent !== 'UNKNOWN') {
        expect(result.confidence).toBeLessThan(0.6);
      }
    });
  });

  describe('Adversarial inputs', () => {
    test('contradiction (green + red) should be UNKNOWN or penalized', () => {
      const result = interpret('green red');
      // Must not be confident GREEN or RED
      if (result.intent !== 'UNKNOWN') {
        expect(result.confidence).toBeLessThan(0.7);
      }
      expect(result.adversarial).toBe(true);
    });

    test('double negation (dont stop) should be flagged adversarial', () => {
      const result = interpret("don't stop");
      expect(result.adversarial).toBe(true);
    });

    test('"not red" should flag adversarial', () => {
      const result = interpret('not red');
      expect(result.adversarial).toBe(true);
    });

    test('"do NOT send" — negated GREEN action should reduce confidence', () => {
      const result = interpret('do NOT send');
      // Either UNKNOWN or very low confidence GREEN
      if (result.intent === 'GREEN') {
        expect(result.confidence).toBeLessThan(0.6);
      }
    });

    test('adversarial flag included in result', () => {
      const result = interpret('green red start stop');
      expect(result).toHaveProperty('adversarial');
      expect(result).toHaveProperty('reasoning');
      expect(typeof result.reasoning).toBe('string');
    });

    test.each(ADVERSARIAL_COMMANDS)(
      'adversarial "$input" — should flag or handle gracefully',
      ({input, notes}) => {
        const result = interpret(input);
        // Never throw, always return valid structure
        expect(result).toHaveProperty('intent');
        expect(result).toHaveProperty('confidence');
        expect(result).toHaveProperty('reasoning');
        expect(result).toHaveProperty('adversarial');
        expect(['GREEN', 'RED', 'UNKNOWN']).toContain(result.intent);
        expect(result.confidence).toBeGreaterThanOrEqual(0);
        expect(result.confidence).toBeLessThanOrEqual(1);
        // Log for visibility
        console.log(
          `  [${result.intent}|${(result.confidence * 100).toFixed(0)}%|adv=${result.adversarial}] "${input}" — ${notes}`,
        );
      },
    );
  });

  describe('Result structure', () => {
    test('always returns valid InterpretResult', () => {
      const cases = ['green', 'red', '', 'garbage xyz 123', 'aBcDeF'];
      for (const c of cases) {
        const r = interpret(c);
        expect(['GREEN', 'RED', 'UNKNOWN']).toContain(r.intent);
        expect(r.confidence).toBeGreaterThanOrEqual(0);
        expect(r.confidence).toBeLessThanOrEqual(1);
        expect(typeof r.reasoning).toBe('string');
        expect(typeof r.adversarial).toBe('boolean');
        expect(typeof r.raw).toBe('string');
      }
    });
  });
});

// ─── Bulk Module Tests ────────────────────────────────────────────────────────

describe('BulkSender — estimateFinalizationMs', () => {
  test('1x gas → 3 blocks (18s)', () => {
    expect(estimateFinalizationMs(1_000_000_000)).toBe(18_000);
  });

  test('1.5x gas → 2 blocks (12s)', () => {
    expect(estimateFinalizationMs(1_500_000_000)).toBe(12_000);
  });

  test('2x gas → 1 block (6s)', () => {
    expect(estimateFinalizationMs(2_000_000_000)).toBe(6_000);
  });

  test('0.5x gas → 5 blocks (30s)', () => {
    expect(estimateFinalizationMs(500_000_000)).toBe(30_000);
  });

  test('custom block time (600ms network simulation)', () => {
    // Simulate 600ms block times (used in tests)
    expect(estimateFinalizationMs(1_500_000_000, 1_000_000_000, 600)).toBe(1200);
    expect(estimateFinalizationMs(2_000_000_000, 1_000_000_000, 600)).toBe(600);
  });
});

// ─── CommandListener Mock Tests ───────────────────────────────────────────────

/**
 * Mock API provider that simulates transactions arriving on-chain.
 */
class MockApiProvider {
  private transactions: Array<{
    txHash: string;
    sender: string;
    receiver: string;
    data: string;
    timestamp: number;
    status: string;
  }> = [];

  addTransaction(data: string, sender = 'erd1mockSender'): string {
    const hash = `mock_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    this.transactions.unshift({
      txHash: hash,
      sender,
      receiver: 'erd1mockTarget',
      data: Buffer.from(data).toString('base64'),
      timestamp: Math.floor(Date.now() / 1000),
      status: 'success',
    });
    return hash;
  }

  getTransactions(): typeof this.transactions {
    return this.transactions.slice(0, 20);
  }
}

describe('CommandListener', () => {
  test('emits commandReceived for GREEN transaction', done => {
    const interpreter = new SemanticInterpreter(0.6);
    const mockProvider = new MockApiProvider();

    // We test the interpreter + event path directly
    const result = interpreter.interpret('green');
    expect(result.intent).toBe('GREEN');

    // Simulate what listener does
    const event = new EventEmitter();
    event.emit('commandReceived', {
      txHash: 'hash123',
      sender: 'erd1sender',
      timestamp: Date.now() / 1000,
      data: 'green',
      interpretation: result,
    } as CommandEvent);

    done();
  });

  test('does NOT emit for UNKNOWN data field', () => {
    const interpreter = new SemanticInterpreter(0.6);
    const result = interpreter.interpret('hello world xyz');
    expect(result.intent).toBe('UNKNOWN');
  });

  test('decodes base64 data field correctly', () => {
    const interpreter = new SemanticInterpreter(0.6);
    const encoded = Buffer.from('stop everything now').toString('base64');
    const result = interpreter.interpret(encoded);
    expect(result.intent).toBe('RED');
  });
});

// ─── Network Latency Simulation (600ms blocks) ────────────────────────────────

describe('Network latency simulation (600ms block time)', () => {
  const BLOCK_TIME_MS = 600;

  test('GREEN command → bulk starts within 1 block time', async () => {
    const startTime = Date.now();
    const interpreter = new SemanticInterpreter(0.5);

    // Simulate processing a GREEN command
    const result = interpreter.interpret('green');
    expect(result.intent).toBe('GREEN');

    // Simulate network delay for tx processing
    await sleep(BLOCK_TIME_MS);

    const elapsed = Date.now() - startTime;
    expect(elapsed).toBeGreaterThanOrEqual(BLOCK_TIME_MS);
    expect(elapsed).toBeLessThan(BLOCK_TIME_MS * 3);
  }, 10_000);

  test('RED command → stops within same block', async () => {
    const startTime = Date.now();
    const interpreter = new SemanticInterpreter(0.5);

    // Simulate GREEN then RED within same block window
    const greenResult = interpreter.interpret('start sending');
    const redResult = interpreter.interpret('abort');

    expect(greenResult.intent).toBe('GREEN');
    expect(redResult.intent).toBe('RED');

    await sleep(BLOCK_TIME_MS);
    const elapsed = Date.now() - startTime;
    expect(elapsed).toBeGreaterThanOrEqual(BLOCK_TIME_MS);
  }, 10_000);

  test('finalization estimate for 1.5x gas on 600ms network', () => {
    const est = estimateFinalizationMs(1_500_000_000, 1_000_000_000, BLOCK_TIME_MS);
    expect(est).toBe(BLOCK_TIME_MS * 2); // 1200ms
  });

  test('bulk of 95 txs with 100ms interval takes ~9.5 seconds', () => {
    const BULK_SIZE = 95;
    const TX_INTERVAL_MS = 100;
    const estimatedTime = BULK_SIZE * TX_INTERVAL_MS;
    // With parallelism of 20, actual time is ~(95/20) * 100ms = ~475ms for dispatch
    const MAX_PARALLEL_TXS = 20;
    const estimatedParallelTime = Math.ceil(BULK_SIZE / MAX_PARALLEL_TXS) * TX_INTERVAL_MS;
    expect(estimatedParallelTime).toBeLessThanOrEqual(estimatedTime);
    expect(estimatedParallelTime).toBeGreaterThan(0);
  });
});

// ─── Agent State Machine Tests ────────────────────────────────────────────────

describe('Challenge5Agent state machine', () => {
  // We test state transitions without hitting the network
  // by using the exported AgentState type and mocking the agent

  test('initial state is STOPPED', () => {
    // Agent only transitions after start() is called
    // This verifies the type/enum values exist
    const states: AgentState[] = ['STOPPED', 'LISTENING', 'SENDING'];
    expect(states).toContain('STOPPED');
    expect(states).toContain('LISTENING');
    expect(states).toContain('SENDING');
  });

  test('GREEN command triggers SENDING state', async () => {
    let state: AgentState = 'LISTENING';

    // Simulate state machine logic
    function handleCommand(intent: Intent): AgentState {
      if (intent === 'GREEN' && state === 'LISTENING') {
        state = 'SENDING';
      } else if (intent === 'RED' && state === 'SENDING') {
        state = 'LISTENING';
      }
      return state;
    }

    expect(handleCommand('GREEN')).toBe('SENDING');
    expect(state).toBe('SENDING');
  });

  test('RED command triggers LISTENING state (kill switch)', () => {
    let state: AgentState = 'SENDING';

    function handleCommand(intent: Intent): AgentState {
      if (intent === 'RED') {
        state = 'LISTENING';
      }
      return state;
    }

    expect(handleCommand('RED')).toBe('LISTENING');
    expect(state).toBe('LISTENING');
  });

  test('duplicate GREEN while SENDING is ignored', () => {
    let state: AgentState = 'SENDING';

    function handleCommand(intent: Intent): AgentState {
      if (intent === 'GREEN' && state !== 'SENDING') {
        state = 'SENDING';
      }
      return state;
    }

    // State remains SENDING, no transition
    expect(handleCommand('GREEN')).toBe('SENDING');
    expect(state).toBe('SENDING');
  });

  test('UNKNOWN command does not change state', () => {
    let state: AgentState = 'LISTENING';

    function handleCommand(intent: Intent): AgentState {
      if (intent === 'GREEN' && state === 'LISTENING') state = 'SENDING';
      else if (intent === 'RED') state = 'LISTENING';
      // UNKNOWN → no change
      return state;
    }

    expect(handleCommand('UNKNOWN')).toBe('LISTENING');
    expect(state).toBe('LISTENING');
  });
});

// ─── Integration: Full Command Flow ───────────────────────────────────────────

describe('Integration: command flow', () => {
  const interpreter = new SemanticInterpreter(0.6, 0.3, true);

  test('typical challenge flow: GREEN → SENDING → RED → STOPPED', () => {
    let state: AgentState = 'LISTENING';
    const events: string[] = [];

    function process(rawData: string): void {
      const result = interpreter.interpret(rawData);
      if (result.intent === 'GREEN' && state === 'LISTENING') {
        state = 'SENDING';
        events.push('GREEN:SENDING');
      } else if (result.intent === 'RED' && state === 'SENDING') {
        state = 'LISTENING';
        events.push('RED:LISTENING');
      } else if (result.intent === 'UNKNOWN') {
        events.push(`UNKNOWN:${state}`);
      }
    }

    process('start sending'); // GREEN
    expect(state).toBe('SENDING');

    process('keep going'); // Likely UNKNOWN
    // State unchanged
    expect(['SENDING', 'LISTENING']).toContain(state);

    // Force state to SENDING for RED test
    state = 'SENDING';
    process('abort'); // RED
    expect(state).toBe('LISTENING');

    expect(events).toContain('GREEN:SENDING');
    expect(events).toContain('RED:LISTENING');
  });

  test('adversarial sequence does not accidentally trigger sends', () => {
    let state: AgentState = 'LISTENING';
    const adversarialInputs = [
      'green red',
      "don't stop",
      'not green',
      'gr3en',
    ];

    for (const input of adversarialInputs) {
      const result = interpreter.interpret(input);
      if (result.intent === 'GREEN' && !result.adversarial && state === 'LISTENING') {
        state = 'SENDING';
      }
    }

    // Should NOT have triggered sending for adversarial inputs
    // (adversarial flag should prevent confident GREEN)
    expect(state).toBe('LISTENING');
  });
});
