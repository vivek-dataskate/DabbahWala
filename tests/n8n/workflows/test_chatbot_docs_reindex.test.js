'use strict';
/**
 * Tests for chatbot_docs_reindex.json
 *
 * Workflow: [Chatbot] Docs Reindex
 * Schedule: Every Monday at 2 AM
 *
 * Nodes under test:
 *   IF:   "Check Success"  — $json.status === 'ok'
 *                            TRUE  → Log Success (Code)
 *                            FALSE → Log Failure (Code)
 *   Code: "Log Success"   — console.log, returns $input.all() unchanged
 *   Code: "Log Failure"   — console.error, returns $input.all() unchanged
 *
 * Flow:
 *   Trigger → Reindex Chatbot Docs (HTTP POST /api/chatbot/reindex)
 *           → Check Success → Log Success (TRUE)
 *                           → Log Failure (FALSE)
 *
 * Cleanup: pure JS unit tests — no external state to clean up.
 */

const { runCode, evalIf, loadWorkflow, getCodeNode, getIfConditions } = require('../helpers/runtime');

let wf;
let ifCheckSuccess;
let codeLogSuccess;
let codeLogFailure;

beforeAll(() => {
  wf              = loadWorkflow('chatbot_docs_reindex.json');
  ifCheckSuccess  = getIfConditions(wf, 'Check Success');
  codeLogSuccess  = getCodeNode(wf, 'Log Success');
  codeLogFailure  = getCodeNode(wf, 'Log Failure');
});

afterAll(() => {
  wf = null;
});

// ─── Workflow metadata ────────────────────────────────────────────────────────

describe('Workflow metadata', () => {
  test('workflow name is correct', () => {
    expect(wf.name).toBe('[Chatbot] Docs Reindex');
  });

  test('schedule trigger fires every Monday at 2 AM', () => {
    const trigger = wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger');
    expect(trigger).toBeDefined();
    const interval = trigger.parameters.rule.interval[0];
    expect(interval.field).toBe('weeks');
    expect(interval.weeksInterval).toBe(1);
    expect(interval.triggerAtDay).toContain(1); // 1 = Monday
    expect(interval.triggerAtHour).toBe(2);
    expect(interval.triggerAtMinute).toBe(0);
  });

  test('HTTP request targets the reindex endpoint', () => {
    const http = wf.nodes.find(n => n.name === 'Reindex Chatbot Docs');
    expect(http).toBeDefined();
    expect(http.parameters.url).toBe(
      'https://dabbahwala-latest.onrender.com/api/chatbot/reindex'
    );
    expect(http.parameters.method).toBe('POST');
  });

  test('HTTP request has 60 second timeout', () => {
    const http = wf.nodes.find(n => n.name === 'Reindex Chatbot Docs');
    expect(http.parameters.options.timeout).toBe(60000);
  });
});

// ─── IF: Check Success ────────────────────────────────────────────────────────

describe('Check Success — IF node', () => {
  // TRUE branch → Log Success
  test('TRUE branch: status="ok" → reindex succeeded', () => {
    expect(evalIf(ifCheckSuccess, { status: 'ok' })).toBe(true);
  });

  // FALSE branch → Log Failure
  test('FALSE branch: status="error" → reindex failed', () => {
    expect(evalIf(ifCheckSuccess, { status: 'error' })).toBe(false);
  });

  test('FALSE branch: status="failed" → reindex failed', () => {
    expect(evalIf(ifCheckSuccess, { status: 'failed' })).toBe(false);
  });

  test('FALSE branch: status="timeout" → reindex failed', () => {
    expect(evalIf(ifCheckSuccess, { status: 'timeout' })).toBe(false);
  });

  test('FALSE branch: status missing → reindex failed', () => {
    expect(evalIf(ifCheckSuccess, {})).toBe(false);
  });

  test('FALSE branch: status=null → reindex failed', () => {
    expect(evalIf(ifCheckSuccess, { status: null })).toBe(false);
  });

  test('FALSE branch: status="" (empty string) → reindex failed', () => {
    expect(evalIf(ifCheckSuccess, { status: '' })).toBe(false);
  });

  test('FALSE branch: status="OK" (uppercase) does not match', () => {
    expect(evalIf(ifCheckSuccess, { status: 'OK' })).toBe(false);
  });

  test('FALSE branch: status="Ok" (mixed case) does not match', () => {
    expect(evalIf(ifCheckSuccess, { status: 'Ok' })).toBe(false);
  });

  test('TRUE branch: status="ok" with extra fields → still passes', () => {
    expect(evalIf(ifCheckSuccess, {
      status: 'ok',
      total_chunks: 42,
      files_indexed: ['faq.md', 'menu.md'],
    })).toBe(true);
  });
});

// ─── IF node structure validation ────────────────────────────────────────────

describe('Check Success — IF node structure', () => {
  test('IF node uses string equals operator', () => {
    const cond = ifCheckSuccess.conditions[0];
    expect(cond.operator.type).toBe('string');
    expect(cond.operator.operation).toBe('equals');
  });

  test('IF node right value is "ok"', () => {
    const cond = ifCheckSuccess.conditions[0];
    expect(cond.rightValue).toBe('ok');
  });

  test('IF node left value references $json.status', () => {
    const cond = ifCheckSuccess.conditions[0];
    expect(cond.leftValue).toContain('$json.status');
  });
});

// ─── Code: Log Success ────────────────────────────────────────────────────────

describe('Log Success — Code node', () => {
  const successInput = {
    status: 'ok',
    total_chunks: 42,
    files_indexed: ['faq.md', 'menu.md'],
  };

  test('returns input items unchanged (pass-through)', async () => {
    const result = await runCode(codeLogSuccess, { inputItems: [successInput] });
    expect(result).toHaveLength(1);
    expect(result[0].json.status).toBe('ok');
    expect(result[0].json.total_chunks).toBe(42);
    expect(result[0].json.files_indexed).toEqual(['faq.md', 'menu.md']);
  });

  test('does not mutate the input data', async () => {
    const original = { status: 'ok', total_chunks: 10 };
    const result = await runCode(codeLogSuccess, { inputItems: [original] });
    expect(result[0].json.total_chunks).toBe(10);
  });

  test('preserves all input fields', async () => {
    const input = { status: 'ok', total_chunks: 7, files_indexed: ['chatbot.md'], extra: 'data' };
    const result = await runCode(codeLogSuccess, { inputItems: [input] });
    expect(result[0].json).toMatchObject(input);
  });

  test('works with empty files_indexed array', async () => {
    const input = { status: 'ok', total_chunks: 0, files_indexed: [] };
    const result = await runCode(codeLogSuccess, { inputItems: [input] });
    expect(result[0].json.files_indexed).toEqual([]);
    expect(result[0].json.total_chunks).toBe(0);
  });

  test('works with missing files_indexed field', async () => {
    const input = { status: 'ok', total_chunks: 5 };
    const result = await runCode(codeLogSuccess, { inputItems: [input] });
    expect(result[0].json.status).toBe('ok');
    expect(result[0].json.total_chunks).toBe(5);
  });

  test('returns same number of items as input', async () => {
    const items = [
      { status: 'ok', total_chunks: 1 },
      { status: 'ok', total_chunks: 2 },
    ];
    const result = await runCode(codeLogSuccess, { inputItems: items });
    expect(result).toHaveLength(2);
  });

  test('handles large chunk count', async () => {
    const input = { status: 'ok', total_chunks: 9999, files_indexed: ['big.md'] };
    const result = await runCode(codeLogSuccess, { inputItems: [input] });
    expect(result[0].json.total_chunks).toBe(9999);
  });
});

// ─── Code: Log Failure ────────────────────────────────────────────────────────

describe('Log Failure — Code node', () => {
  const failureInput = {
    status: 'error',
    message: 'No documents found',
  };

  test('returns input items unchanged (pass-through)', async () => {
    const result = await runCode(codeLogFailure, { inputItems: [failureInput] });
    expect(result).toHaveLength(1);
    expect(result[0].json.status).toBe('error');
    expect(result[0].json.message).toBe('No documents found');
  });

  test('does not mutate the input data', async () => {
    const input = { status: 'error', code: 500 };
    const result = await runCode(codeLogFailure, { inputItems: [input] });
    expect(result[0].json.code).toBe(500);
  });

  test('preserves all input fields on failure', async () => {
    const input = { status: 'error', message: 'Drive API timeout', trace: 'stack here' };
    const result = await runCode(codeLogFailure, { inputItems: [input] });
    expect(result[0].json).toMatchObject(input);
  });

  test('handles failed status with missing message field', async () => {
    const input = { status: 'error' };
    const result = await runCode(codeLogFailure, { inputItems: [input] });
    expect(result[0].json.status).toBe('error');
  });

  test('handles timeout failure payload', async () => {
    const input = { status: 'timeout', message: 'Request exceeded 60s', detail: null };
    const result = await runCode(codeLogFailure, { inputItems: [input] });
    expect(result[0].json.status).toBe('timeout');
    expect(result[0].json.message).toBe('Request exceeded 60s');
  });

  test('returns same number of items as input', async () => {
    const items = [
      { status: 'error', message: 'err1' },
      { status: 'error', message: 'err2' },
    ];
    const result = await runCode(codeLogFailure, { inputItems: items });
    expect(result).toHaveLength(2);
  });

  test('handles empty input json', async () => {
    const result = await runCode(codeLogFailure, { inputItems: [{}] });
    expect(result).toHaveLength(1);
  });
});

// ─── Flow integration ─────────────────────────────────────────────────────────

describe('Flow: Reindex result → Check Success → Log Success / Log Failure', () => {
  test('status=ok routes to Log Success and returns data unchanged', async () => {
    const apiResponse = { status: 'ok', total_chunks: 42, files_indexed: ['faq.md', 'menu.md'] };

    // Step 1: IF routes to Log Success (TRUE branch)
    expect(evalIf(ifCheckSuccess, apiResponse)).toBe(true);

    // Step 2: Log Success returns data unchanged
    const logResult = await runCode(codeLogSuccess, { inputItems: [apiResponse] });
    expect(logResult[0].json.status).toBe('ok');
    expect(logResult[0].json.total_chunks).toBe(42);
  });

  test('status=error routes to Log Failure and returns data unchanged', async () => {
    const apiResponse = { status: 'error', message: 'No documents found' };

    // Step 1: IF routes to Log Failure (FALSE branch)
    expect(evalIf(ifCheckSuccess, apiResponse)).toBe(false);

    // Step 2: Log Failure returns data unchanged
    const logResult = await runCode(codeLogFailure, { inputItems: [apiResponse] });
    expect(logResult[0].json.status).toBe('error');
    expect(logResult[0].json.message).toBe('No documents found');
  });

  test('status=ok with zero chunks still succeeds', async () => {
    const apiResponse = { status: 'ok', total_chunks: 0, files_indexed: [] };
    expect(evalIf(ifCheckSuccess, apiResponse)).toBe(true);
    const result = await runCode(codeLogSuccess, { inputItems: [apiResponse] });
    expect(result[0].json.total_chunks).toBe(0);
  });

  test('HTTP 500 response body routes to failure path', async () => {
    const httpError = { status: 'error', detail: 'Internal Server Error', statusCode: 500 };
    expect(evalIf(ifCheckSuccess, httpError)).toBe(false);
    const result = await runCode(codeLogFailure, { inputItems: [httpError] });
    expect(result[0].json.statusCode).toBe(500);
  });

  test('multiple files indexed — all preserved through Log Success', async () => {
    const apiResponse = {
      status: 'ok',
      total_chunks: 123,
      files_indexed: ['faq.md', 'menu.md', 'delivery.md', 'about.md'],
    };
    expect(evalIf(ifCheckSuccess, apiResponse)).toBe(true);
    const result = await runCode(codeLogSuccess, { inputItems: [apiResponse] });
    expect(result[0].json.files_indexed).toHaveLength(4);
  });
});
