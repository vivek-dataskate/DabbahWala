'use strict';
/**
 * Tests for [Intelligence] AI Stack — agent_orchestration_cron.json
 *
 * Covers:
 *  - Code: Summarise Cycle  — processed/errors/actions_queued/summary output
 *  - IF:   Contacts Processed? — TRUE (processed > 0) / FALSE (processed = 0)
 *  - HTTP: Run Full Agent Cycle — POST, correct URL, 10-min timeout
 */

const { loadWorkflow, getCodeNode, getIfConditions, getNode, runCode, evalIf } = require('../helpers/runtime');

let wf, code, ifc;
beforeAll(() => {
  wf   = loadWorkflow('agent_orchestration_cron.json');
  code = getCodeNode(wf, 'Summarise Cycle');
  ifc  = getIfConditions(wf, 'Contacts Processed?');
});

// ─── Summarise Cycle — Code node ─────────────────────────────────────────────

describe('Summarise Cycle — Code node', () => {
  test('outputs processed, errors, actions_queued correctly', async () => {
    const item = {
      processed: 5,
      errors: ['e1', 'e2'],
      results: [
        { chosen_action: 'send_sms' },
        { chosen_action: 'none' },
        { chosen_action: 'move_campaign' },
        { chosen_action: 'none' },
      ],
    };
    const [out] = await runCode(code, { inputItems: [item] });
    expect(out.json.processed).toBe(5);
    expect(out.json.errors).toBe(2);
    expect(out.json.actions_queued).toBe(2);  // 'none' excluded
  });

  test('summary string includes processed count and actions queued', async () => {
    const item = { processed: 3, errors: [], results: [{ chosen_action: 'send_sms' }] };
    const [out] = await runCode(code, { inputItems: [item] });
    expect(out.json.summary).toContain('3 contacts processed');
    expect(out.json.summary).toContain('1 actions queued');
  });

  test('handles empty results and errors arrays', async () => {
    const item = { processed: 0, errors: [], results: [] };
    const [out] = await runCode(code, { inputItems: [item] });
    expect(out.json.actions_queued).toBe(0);
    expect(out.json.errors).toBe(0);
    expect(out.json.processed).toBe(0);
  });

  test('chosen_action=none does not count as queued action', async () => {
    const item = {
      processed: 2,
      errors: [],
      results: [{ chosen_action: 'none' }, { chosen_action: 'none' }],
    };
    const [out] = await runCode(code, { inputItems: [item] });
    expect(out.json.actions_queued).toBe(0);
  });
});

// ─── Contacts Processed? — IF node ───────────────────────────────────────────

describe('Contacts Processed? — IF node', () => {
  test('TRUE: processed > 0', () => {
    expect(evalIf(ifc, { processed: 1 })).toBe(true);
  });
  test('TRUE: processed = 10', () => {
    expect(evalIf(ifc, { processed: 10 })).toBe(true);
  });
  test('FALSE: processed = 0', () => {
    expect(evalIf(ifc, { processed: 0 })).toBe(false);
  });
  test('FALSE: processed missing', () => {
    expect(evalIf(ifc, {})).toBe(false);
  });
});

// ─── HTTP node config ─────────────────────────────────────────────────────────

describe('Run Full Agent Cycle — HTTP node', () => {
  test('method is POST', () => {
    const node = getNode(wf, 'Run Full Agent Cycle');
    expect(node.parameters.method).toBe('POST');
  });
  test('URL targets /api/agents/cycle/run-daily-sweep', () => {
    const node = getNode(wf, 'Run Full Agent Cycle');
    expect(node.parameters.url).toContain('/api/agents/cycle/run-daily-sweep');
  });
  test('timeout is at least 10 minutes (600000 ms)', () => {
    const node = getNode(wf, 'Run Full Agent Cycle');
    expect(node.parameters.options?.timeout).toBeGreaterThanOrEqual(600000);
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('has a schedule trigger', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeDefined();
  });
  test('has Contacts Processed? IF node', () => {
    expect(getNode(wf, 'Contacts Processed?').type).toBe('n8n-nodes-base.if');
  });
  test('has No Eligible Contacts terminal node', () => {
    expect(getNode(wf, 'No Eligible Contacts')).toBeDefined();
  });
});
