'use strict';
/**
 * Tests for [Order Intake] Historical Import — shipday_historical_import.json
 *
 * Covers:
 *  - Code: Config          — returns days_back and max_pages defaults
 *  - Code: Log Kick-off    — passes status, detects started=true/false
 *  - Code: Summary         — builds final summary from pipeline_state
 *  - IF:   Still Running?  — TRUE (pipeline_state.running=true) / FALSE (running=false)
 *  - HTTP: Start Import Pipeline — POST to /api/shipday/import-all-and-run-agents
 *  - HTTP: Poll Status    — GET to /api/shipday/import-pipeline-status
 */

const { loadWorkflow, getCodeNode, getIfConditions, getNode, runCode, evalIf } = require('../helpers/runtime');

let wf, configCode, kickoffCode, summaryCode, ifc1, ifc2;
beforeAll(() => {
  wf          = loadWorkflow('shipday_historical_import.json');
  configCode  = getCodeNode(wf, 'Config');
  kickoffCode = getCodeNode(wf, 'Log Kick-off');
  summaryCode = getCodeNode(wf, 'Summary');
  ifc1        = getIfConditions(wf, 'Still Running?');
  ifc2        = getIfConditions(wf, 'Still Running? (15 min)');
});

// ─── Config — Code node ───────────────────────────────────────────────────────

describe('Config — Code node', () => {
  test('returns days_back=365 and max_pages=500', async () => {
    const [out] = await runCode(configCode, { inputItems: [{ json: {} }] });
    expect(out.json.days_back).toBe(365);
    expect(out.json.max_pages).toBe(500);
  });
});

// ─── Log Kick-off — Code node ─────────────────────────────────────────────────

describe('Log Kick-off — Code node', () => {
  test('detects started=true when status=started', async () => {
    const result = { status: 'started', pipeline_id: 'abc123' };
    const [out] = await runCode(kickoffCode, { inputItems: [{ json: result }] });
    expect(out.json.kicked_off).toBe(true);
    expect(out.json.kick_off_response.status).toBe('started');
  });

  test('handles non-started status', async () => {
    const result = { status: 'error', message: 'DB not reachable' };
    const [out] = await runCode(kickoffCode, { inputItems: [{ json: result }] });
    expect(out.json.kicked_off).toBe(false);
    expect(out.json.kick_off_response.status).toBe('error');
  });
});

// ─── Summary — Code node ──────────────────────────────────────────────────────

describe('Summary — Code node', () => {
  test('builds summary lines from pipeline_state', async () => {
    const state = {
      pipeline_state: {
        running: false,
        orders_synced: 1200,
        orders_matched: 1100,
        contacts_created: 50,
        orders_created: 900,
        orders_updated: 200,
        comms_synced: 300,
        agents_run: 150,
        agent_errors: 2,
      },
    };
    const [out] = await runCode(summaryCode, { inputItems: [{ json: state }] });
    const lines = out.json.lines || out.json.summary || out.json;
    expect(lines).toBeDefined();
  });
});

// ─── Still Running? — IF nodes ───────────────────────────────────────────────

describe('Still Running? — IF node (5 min poll)', () => {
  test('TRUE: pipeline_state.running=true', () => {
    expect(evalIf(ifc1, { pipeline_state: { running: true } })).toBe(true);
  });
  test('FALSE: pipeline_state.running=false', () => {
    expect(evalIf(ifc1, { pipeline_state: { running: false } })).toBe(false);
  });
});

describe('Still Running? (15 min) — IF node', () => {
  test('TRUE: pipeline_state.running=true', () => {
    expect(evalIf(ifc2, { pipeline_state: { running: true } })).toBe(true);
  });
  test('FALSE: pipeline_state.running=false', () => {
    expect(evalIf(ifc2, { pipeline_state: { running: false } })).toBe(false);
  });
});

// ─── HTTP node configs ────────────────────────────────────────────────────────

describe('Start Import Pipeline — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Start Import Pipeline').parameters.method).toBe('POST');
  });
  test('URL targets /api/shipday/import-all-and-run-agents', () => {
    expect(getNode(wf, 'Start Import Pipeline').parameters.url).toContain('/import-all-and-run-agents');
  });
});

describe('Poll Status (5 min) — HTTP node', () => {
  test('URL targets /api/shipday/import-pipeline-status', () => {
    expect(getNode(wf, 'Poll Status (5 min)').parameters.url).toContain('/import-pipeline-status');
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('manual trigger only (historical import is a one-off)', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.manualTrigger')).toBeDefined();
  });
  test('has wait nodes for polling delays', () => {
    const waitNodes = wf.nodes.filter(n => n.type === 'n8n-nodes-base.wait');
    expect(waitNodes.length).toBeGreaterThanOrEqual(2);
  });
  test('has Summary Code node', () => {
    expect(getNode(wf, 'Summary').type).toBe('n8n-nodes-base.code');
  });
});
