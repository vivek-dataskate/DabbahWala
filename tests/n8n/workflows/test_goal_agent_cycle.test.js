'use strict';
/**
 * Tests for [Growth] Goal Agent — goal_agent_cycle.json
 *
 * Covers:
 *  - Code: Log Result — captures key output fields, adds timestamp
 *  - HTTP: Run Goal Agent Full Cycle — POST, correct URL, 5-min timeout
 *  - Workflow structure
 */

const { loadWorkflow, getCodeNode, getNode, runCode } = require('../helpers/runtime');

let wf, logCode;
beforeAll(() => {
  wf      = loadWorkflow('goal_agent_cycle.json');
  logCode = getCodeNode(wf, 'Log Result');
});

// ─── Log Result — Code node ───────────────────────────────────────────────────

describe('Log Result — Code node', () => {
  test('captures all goal agent output fields', async () => {
    const result = {
      timestamp: '2026-01-01T09:00:00Z',
      phase: 'complete',
      experiments_created: 2,
      experiments_started: 1,
      contacts_enrolled: 50,
      experiments_concluded: 0,
      signals_discovered: 3,
      orders_attributed: 7,
    };
    const [out] = await runCode(logCode, {
      nodeData: { 'Run Goal Agent Full Cycle': [{ json: result }] },
    });
    expect(out.json.experiments_created).toBe(2);
    expect(out.json.contacts_enrolled).toBe(50);
    expect(out.json.signals_discovered).toBe(3);
    expect(out.json.timestamp).toBe('2026-01-01T09:00:00Z');
  });

  test('includes summary string', async () => {
    const result = {
      timestamp: '2026-01-01T09:00:00Z',
      experiments_created: 2,
      experiments_started: 1,
      contacts_enrolled: 50,
      experiments_concluded: 1,
      signals_discovered: 3,
      orders_attributed: 7,
    };
    const [out] = await runCode(logCode, {
      nodeData: { 'Run Goal Agent Full Cycle': [{ json: result }] },
    });
    expect(out.json.summary).toContain('2 new experiments');
    expect(out.json.summary).toContain('50 contacts');
  });

  test('handles zero output gracefully', async () => {
    const result = {
      timestamp: null,
      experiments_created: 0, experiments_started: 0,
      contacts_enrolled: 0, experiments_concluded: 0,
      signals_discovered: 0, orders_attributed: 0,
    };
    const [out] = await runCode(logCode, {
      nodeData: { 'Run Goal Agent Full Cycle': [{ json: result }] },
    });
    expect(out.json.experiments_created).toBe(0);
  });
});

// ─── HTTP node config ─────────────────────────────────────────────────────────

describe('Run Goal Agent Full Cycle — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Run Goal Agent Full Cycle').parameters.method).toBe('POST');
  });
  test('URL targets /api/goal-agent/run', () => {
    expect(getNode(wf, 'Run Goal Agent Full Cycle').parameters.url).toContain('/goal-agent/run');
  });
  test('timeout is at least 5 minutes (300000 ms)', () => {
    expect(getNode(wf, 'Run Goal Agent Full Cycle').parameters.options?.timeout).toBeGreaterThanOrEqual(300000);
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('has a schedule trigger', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeDefined();
  });
  test('has Log Result Code node', () => {
    expect(getNode(wf, 'Log Result').type).toBe('n8n-nodes-base.code');
  });
});
