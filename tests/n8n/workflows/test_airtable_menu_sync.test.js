'use strict';
/**
 * Tests for [Menu] Catalog Sync — airtable_menu_sync.json
 *
 * Covers:
 *  - IF:   Has Errors? — TRUE (errors > 0) / FALSE (errors = 0)
 *  - Code: Log Success — passes through data unchanged
 *  - Code: Log Errors  — passes through data unchanged
 *  - HTTP: Sync Airtable → Postgres — POST, correct URL, timeout
 */

const { loadWorkflow, getCodeNode, getIfConditions, getNode, runCode, evalIf } = require('../helpers/runtime');

let wf, logSuccess, logErrors, ifc;
beforeAll(() => {
  wf         = loadWorkflow('airtable_menu_sync.json');
  logSuccess = getCodeNode(wf, 'Log Success');
  logErrors  = getCodeNode(wf, 'Log Errors');
  ifc        = getIfConditions(wf, 'Has Errors?');
});

// ─── Has Errors? — IF node ────────────────────────────────────────────────────

describe('Has Errors? — IF node', () => {
  test('FALSE: errors = 0 → success path', () => {
    expect(evalIf(ifc, { errors: 0 })).toBe(false);
  });
  test('TRUE: errors > 0 → error path', () => {
    expect(evalIf(ifc, { errors: 3 })).toBe(true);
  });
  test('FALSE: errors missing', () => {
    expect(evalIf(ifc, {})).toBe(false);
  });
  test('TRUE: errors = 1', () => {
    expect(evalIf(ifc, { errors: 1 })).toBe(true);
  });
});

// ─── Log Success — Code node ─────────────────────────────────────────────────

describe('Log Success — Code node', () => {
  test('passes sync data through', async () => {
    const data = { upserted: 10, discarded: 2, errors: 0 };
    const [out] = await runCode(logSuccess, { inputItems: [{ json: data }] });
    expect(out.json.upserted).toBe(10);
    expect(out.json.discarded).toBe(2);
  });
});

// ─── Log Errors — Code node ──────────────────────────────────────────────────

describe('Log Errors — Code node', () => {
  test('passes error data through', async () => {
    const data = { upserted: 5, discarded: 0, errors: 2 };
    const [out] = await runCode(logErrors, { inputItems: [{ json: data }] });
    expect(out.json.upserted).toBe(5);
    expect(out.json.errors).toBe(2);
  });
});

// ─── HTTP node config ─────────────────────────────────────────────────────────

describe('Sync Airtable → Postgres — HTTP node', () => {
  test('method is POST', () => {
    const node = getNode(wf, 'Sync Airtable → Postgres');
    expect(node.parameters.method).toBe('POST');
  });
  test('URL targets /api/menu/sync', () => {
    const node = getNode(wf, 'Sync Airtable → Postgres');
    expect(node.parameters.url).toContain('/api/menu/sync');
  });
  test('timeout configured', () => {
    const node = getNode(wf, 'Sync Airtable → Postgres');
    expect(node.parameters.options?.timeout).toBeGreaterThan(0);
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('has a schedule trigger', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeDefined();
  });
  test('has Has Errors? IF node', () => {
    expect(getNode(wf, 'Has Errors?').type).toBe('n8n-nodes-base.if');
  });
});
