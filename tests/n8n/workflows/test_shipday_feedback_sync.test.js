'use strict';
/**
 * Tests for [Order Intake] Feedback Sync — shipday_feedback_sync.json
 *
 * Covers:
 *  - Code: Log Results           — builds log summary, preserves run_lifecycle flag
 *  - IF:   New Feedback Found?   — TRUE (run_lifecycle=true) / FALSE (run_lifecycle=false)
 *  - HTTP: Sync Shipday Feedback — POST to /api/shipday/sync-feedback, days_back=3
 *  - HTTP: Run Lifecycle Cycle   — POST to /api/lifecycle/run
 */

const { loadWorkflow, getCodeNode, getIfConditions, getNode, runCode, evalIf } = require('../helpers/runtime');

let wf, logCode, ifc;
beforeAll(() => {
  wf      = loadWorkflow('shipday_feedback_sync.json');
  logCode = getCodeNode(wf, 'Log Results');
  ifc     = getIfConditions(wf, 'New Feedback Found?');
});

// ─── Log Results — Code node ─────────────────────────────────────────────────

describe('Log Results — Code node', () => {
  test('passes feedback stats through', async () => {
    const result = {
      state: { orders_checked: 50, orders_with_comms: 10 },
      feedback_positive: 8,
      feedback_negative: 2,
      opportunities_created: 3,
      run_lifecycle: true,
    };
    const [out] = await runCode(logCode, { inputItems: [{ json: result }] });
    expect(out.json.run_lifecycle).toBe(true);
  });

  test('run_lifecycle=false passes through', async () => {
    const result = { state: {}, feedback_positive: 0, feedback_negative: 0, run_lifecycle: false };
    const [out] = await runCode(logCode, { inputItems: [{ json: result }] });
    expect(out.json.run_lifecycle).toBe(false);
  });
});

// ─── New Feedback Found? — IF node ───────────────────────────────────────────

describe('New Feedback Found? — IF node', () => {
  test('TRUE: run_lifecycle=true → run lifecycle cycle', () => {
    expect(evalIf(ifc, { run_lifecycle: true })).toBe(true);
  });
  test('FALSE: run_lifecycle=false → skip', () => {
    expect(evalIf(ifc, { run_lifecycle: false })).toBe(false);
  });
  test('FALSE: run_lifecycle missing', () => {
    expect(evalIf(ifc, {})).toBe(false);
  });
});

// ─── HTTP node configs ────────────────────────────────────────────────────────

describe('Sync Shipday Feedback — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Sync Shipday Feedback').parameters.method).toBe('POST');
  });
  test('URL targets /api/shipday/sync-feedback', () => {
    expect(getNode(wf, 'Sync Shipday Feedback').parameters.url).toContain('/shipday/sync-feedback');
  });
  test('sends days_back=3 in body', () => {
    const node = getNode(wf, 'Sync Shipday Feedback');
    const body = node.parameters.jsonBody || JSON.stringify(node.parameters.body || {});
    const parsed = typeof body === 'string' ? JSON.parse(body) : body;
    expect(parsed.days_back).toBe(3);
  });
});

describe('Run Lifecycle Cycle — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Run Lifecycle Cycle').parameters.method).toBe('POST');
  });
  test('URL targets /api/lifecycle/run', () => {
    expect(getNode(wf, 'Run Lifecycle Cycle').parameters.url).toContain('/lifecycle/run');
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('has an hourly schedule trigger', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeDefined();
  });
  test('has No New Feedback — Skip terminal node', () => {
    expect(getNode(wf, 'No New Feedback — Skip')).toBeDefined();
  });
});
