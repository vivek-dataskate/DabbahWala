'use strict';
/**
 * Tests for [Intelligence] Lapsed Re-engagement — lapsed_customer_cycle.json
 *
 * Covers:
 *  - Code: Log Results      — builds action_summary, sets has_actions flag
 *  - IF:   Any Actions Taken? — TRUE (has_actions=true) / FALSE (has_actions=false)
 *  - HTTP: Run Lapsed Customer Cycle — POST, correct URL, 10-min timeout
 */

const { loadWorkflow, getCodeNode, getIfConditions, getNode, runCode, evalIf } = require('../helpers/runtime');

let wf, logCode, ifc;
beforeAll(() => {
  wf      = loadWorkflow('lapsed_customer_cycle.json');
  logCode = getCodeNode(wf, 'Log Results');
  ifc     = getIfConditions(wf, 'Any Actions Taken?');
});

// ─── Log Results — Code node ─────────────────────────────────────────────────

describe('Log Results — Code node', () => {
  test('builds action_summary from results array', async () => {
    const result = {
      processed: 5,
      errors: [],
      results: [
        { chosen_action: 'send_sms' },
        { chosen_action: 'move_campaign' },
        { chosen_action: 'send_sms' },
        { chosen_action: 'none' },
      ],
    };
    const [out] = await runCode(logCode, { inputItems: [{ json: result }] });
    expect(out.json.action_summary.send_sms).toBe(2);
    expect(out.json.action_summary.move_campaign).toBe(1);
    expect(out.json.action_summary.none).toBe(1);
  });

  test('has_actions=true when send_sms > 0', async () => {
    const result = {
      processed: 3,
      errors: [],
      results: [{ chosen_action: 'send_sms' }],
    };
    const [out] = await runCode(logCode, { inputItems: [{ json: result }] });
    expect(out.json.has_actions).toBe(true);
  });

  test('has_actions=false when all actions are none', async () => {
    const result = {
      processed: 2,
      errors: [],
      results: [{ chosen_action: 'none' }, { chosen_action: 'none' }],
    };
    const [out] = await runCode(logCode, { inputItems: [{ json: result }] });
    expect(out.json.has_actions).toBe(false);
  });

  test('has_actions=false when processed=0', async () => {
    const result = { processed: 0, errors: [], results: [] };
    const [out] = await runCode(logCode, { inputItems: [{ json: result }] });
    expect(out.json.has_actions).toBe(false);
  });

  test('has_actions=true when move_campaign > 0', async () => {
    const result = {
      processed: 1,
      errors: [],
      results: [{ chosen_action: 'move_campaign' }],
    };
    const [out] = await runCode(logCode, { inputItems: [{ json: result }] });
    expect(out.json.has_actions).toBe(true);
  });
});

// ─── Any Actions Taken? — IF node ────────────────────────────────────────────

describe('Any Actions Taken? — IF node', () => {
  test('TRUE: has_actions=true', () => {
    expect(evalIf(ifc, { has_actions: true })).toBe(true);
  });
  test('FALSE: has_actions=false', () => {
    expect(evalIf(ifc, { has_actions: false })).toBe(false);
  });
  test('FALSE: has_actions missing', () => {
    expect(evalIf(ifc, {})).toBe(false);
  });
});

// ─── HTTP node config ─────────────────────────────────────────────────────────

describe('Run Lapsed Customer Cycle — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Run Lapsed Customer Cycle').parameters.method).toBe('POST');
  });
  test('URL targets /api/agents/cycle/run-all-lapsed', () => {
    expect(getNode(wf, 'Run Lapsed Customer Cycle').parameters.url).toContain('/run-all-lapsed');
  });
  test('timeout is at least 10 minutes (600000 ms)', () => {
    expect(getNode(wf, 'Run Lapsed Customer Cycle').parameters.options?.timeout).toBeGreaterThanOrEqual(600000);
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('has a daily schedule trigger', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeDefined();
  });
  test('has No Actions — Skip terminal node', () => {
    expect(getNode(wf, 'No Actions — Skip')).toBeDefined();
  });
  test('has Cycle Complete terminal node', () => {
    expect(getNode(wf, 'Cycle Complete')).toBeDefined();
  });
});
