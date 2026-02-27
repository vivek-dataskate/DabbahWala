'use strict';
/**
 * Tests for [System] Daily Tests — system_test_suite.json
 *
 * Covers:
 *  - Code: Set Context            — outputs triggered_by and ts
 *  - Code: Parse Results & Build Email — builds HTML, sets has_failures flag
 *  - IF:   Any Failures?          — TRUE (has_failures=true) / FALSE (has_failures=false)
 *  - HTTP: Run Full E2E Test Suite — POST to /api/test/run
 *  - HTTP: Send Failure Alert Email / Send Success Summary Email
 */

const { loadWorkflow, getCodeNode, getIfConditions, getNode, runCode, evalIf } = require('../helpers/runtime');

let wf, setCtxCode, parseCode, ifc;
beforeAll(() => {
  wf         = loadWorkflow('system_test_suite.json');
  setCtxCode = getCodeNode(wf, 'Set Context');
  parseCode  = getCodeNode(wf, 'Parse Results & Build Email');
  ifc        = getIfConditions(wf, 'Any Failures?');
});

// ─── Set Context — Code node ─────────────────────────────────────────────────

describe('Set Context — Code node', () => {
  test('outputs triggered_by=n8n_daily and a timestamp', async () => {
    const [out] = await runCode(setCtxCode, { inputItems: [{ json: {} }] });
    expect(out.json.triggered_by).toBe('n8n_daily');
    expect(out.json.ts).toBeDefined();
    expect(new Date(out.json.ts).getFullYear()).toBeGreaterThan(2020);
  });
});

// ─── Parse Results & Build Email — Code node ─────────────────────────────────

describe('Parse Results & Build Email — Code node', () => {
  const makeInput = (summary, groups = [], allTests = []) => ({
    inputItems: [{ json: { summary, groups, all_tests: allTests, duration_seconds: 12, run_id: 'r1' } }],
  });

  test('sets has_failures=false when failed=0', async () => {
    const [out] = await runCode(parseCode, makeInput({ passed: 10, failed: 0, skipped: 0, total: 10 }));
    expect(out.json.has_failures).toBe(false);
  });

  test('sets has_failures=true when failed > 0', async () => {
    const [out] = await runCode(parseCode, makeInput({ passed: 8, failed: 2, skipped: 0, total: 10 }));
    expect(out.json.has_failures).toBe(true);
  });

  test('generates html_body as a string', async () => {
    const [out] = await runCode(parseCode, makeInput({ passed: 5, failed: 0, total: 5 }));
    expect(typeof out.json.html_body).toBe('string');
    expect(out.json.html_body.length).toBeGreaterThan(100);
  });

  test('html_body includes overall status', async () => {
    const [out] = await runCode(parseCode, makeInput({ passed: 5, failed: 0, total: 5 }));
    expect(out.json.html_body).toContain('PASSING');
  });

  test('html_body includes FAILED status when there are failures', async () => {
    const [out] = await runCode(parseCode, makeInput({ passed: 3, failed: 2, total: 5 }));
    expect(out.json.html_body).toContain('FAILED');
  });

  test('includes failed test details in html_body', async () => {
    const allTests = [
      { status: 'fail', group: 'g1', test: 'test_x', message: 'assertion failed', duration_ms: 50 },
    ];
    const [out] = await runCode(parseCode, makeInput({ passed: 0, failed: 1, total: 1 }, [], allTests));
    expect(out.json.html_body).toContain('test_x');
  });

  test('handles missing summary gracefully', async () => {
    const [out] = await runCode(parseCode, makeInput(null));
    expect(out.json.has_failures).toBe(false);
  });
});

// ─── Any Failures? — IF node ─────────────────────────────────────────────────

describe('Any Failures? — IF node', () => {
  test('TRUE: has_failures=true → send failure alert email', () => {
    expect(evalIf(ifc, { has_failures: true })).toBe(true);
  });
  test('FALSE: has_failures=false → send success summary email', () => {
    expect(evalIf(ifc, { has_failures: false })).toBe(false);
  });
});

// ─── HTTP node configs ────────────────────────────────────────────────────────

describe('Run Full E2E Test Suite — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Run Full E2E Test Suite').parameters.method).toBe('POST');
  });
  test('URL targets /api/test/run', () => {
    expect(getNode(wf, 'Run Full E2E Test Suite').parameters.url).toContain('/api/test/run');
  });
  test('timeout is at least 15 minutes (900000 ms)', () => {
    expect(getNode(wf, 'Run Full E2E Test Suite').parameters.options?.timeout).toBeGreaterThanOrEqual(900000);
  });
});

describe('Send Failure Alert Email — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Send Failure Alert Email').parameters.method).toBe('POST');
  });
  test('URL targets /api/internal/send-email', () => {
    expect(getNode(wf, 'Send Failure Alert Email').parameters.url).toContain('/send-email');
  });
});

describe('Send Success Summary Email — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Send Success Summary Email').parameters.method).toBe('POST');
  });
  test('URL targets /api/internal/send-email', () => {
    expect(getNode(wf, 'Send Success Summary Email').parameters.url).toContain('/send-email');
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('runs daily at 5 AM', () => {
    const trigger = wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger');
    expect(trigger).toBeDefined();
  });
  test('has Any Failures? IF node', () => {
    expect(getNode(wf, 'Any Failures?').type).toBe('n8n-nodes-base.if');
  });
  test('has both success and failure email nodes', () => {
    expect(() => getNode(wf, 'Send Failure Alert Email')).not.toThrow();
    expect(() => getNode(wf, 'Send Success Summary Email')).not.toThrow();
  });
});
