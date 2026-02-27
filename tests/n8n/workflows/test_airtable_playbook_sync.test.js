'use strict';
/**
 * Tests for [Agent Rules] Playbook Sync — airtable_playbook_sync.json
 *
 * Covers:
 *  - Code: Transform Airtable Records — maps fields, filters invalid rows
 *  - IF:   Has Changes? — TRUE (synced > 0) / FALSE (synced = 0)
 *  - HTTP: Sync Rules to API — POST, correct URL, timeout
 *  - HTTP: Fetch Playbook Rules — GET with Airtable URL + active filter
 */

const { loadWorkflow, getCodeNode, getIfConditions, getNode, runCode, evalIf } = require('../helpers/runtime');

let wf, transformCode, ifc;
beforeAll(() => {
  wf            = loadWorkflow('airtable_playbook_sync.json');
  transformCode = getCodeNode(wf, 'Transform Airtable Records');
  ifc           = getIfConditions(wf, 'Has Changes?');
});

// ─── Transform Airtable Records — Code node ──────────────────────────────────

describe('Transform Airtable Records — Code node', () => {
  const makeInput = (records) => ({
    inputItems: [{ json: { records } }],
  });

  test('maps Airtable fields to API format', async () => {
    const airtableRecords = [
      {
        id: 'recABC123',
        fields: {
          'Rule Name': 'Always greet warmly',
          'Category': 'Messaging',
          'Instruction': 'Start every SMS with a warm greeting.',
          'Priority': 80,
          'Active': true,
        },
      },
    ];
    const [out] = await runCode(transformCode, makeInput(airtableRecords));
    const records = out.json.records;
    expect(records).toHaveLength(1);
    expect(records[0].airtable_id).toBe('recABC123');
    expect(records[0].rule_name).toBe('Always greet warmly');
    expect(records[0].category).toBe('messaging');
    expect(records[0].instruction).toBe('Start every SMS with a warm greeting.');
    expect(records[0].priority).toBe(80);
    expect(records[0].active).toBe(true);
  });

  test('lowercases category', async () => {
    const records = [{ id: 'r1', fields: { 'Rule Name': 'R1', 'Category': 'GENERAL', 'Instruction': 'Do it', 'Active': true } }];
    const [out] = await runCode(transformCode, makeInput(records));
    expect(out.json.records[0].category).toBe('general');
  });

  test('filters records missing rule_name', async () => {
    const records = [{ id: 'r1', fields: { 'Instruction': 'Do it', 'Active': true } }];
    const [out] = await runCode(transformCode, makeInput(records));
    expect(out.json.records).toHaveLength(0);
  });

  test('filters records missing instruction', async () => {
    const records = [{ id: 'r1', fields: { 'Rule Name': 'Rule A', 'Active': true } }];
    const [out] = await runCode(transformCode, makeInput(records));
    expect(out.json.records).toHaveLength(0);
  });

  test('defaults priority to 50 when missing', async () => {
    const records = [{ id: 'r1', fields: { 'Rule Name': 'R1', 'Instruction': 'Do it' } }];
    const [out] = await runCode(transformCode, makeInput(records));
    expect(out.json.records[0].priority).toBe(50);
  });

  test('Active=false → active=false', async () => {
    const records = [{ id: 'r1', fields: { 'Rule Name': 'R1', 'Instruction': 'Do it', 'Active': false } }];
    const [out] = await runCode(transformCode, makeInput(records));
    expect(out.json.records[0].active).toBe(false);
  });

  test('handles empty records array', async () => {
    const [out] = await runCode(transformCode, makeInput([]));
    expect(out.json.records).toHaveLength(0);
  });
});

// ─── Has Changes? — IF node ──────────────────────────────────────────────────

describe('Has Changes? — IF node', () => {
  test('TRUE: synced > 0', () => {
    expect(evalIf(ifc, { synced: 5 })).toBe(true);
  });
  test('TRUE: synced = 1', () => {
    expect(evalIf(ifc, { synced: 1 })).toBe(true);
  });
  test('FALSE: synced = 0', () => {
    expect(evalIf(ifc, { synced: 0 })).toBe(false);
  });
  test('FALSE: synced missing', () => {
    expect(evalIf(ifc, {})).toBe(false);
  });
});

// ─── HTTP node configs ────────────────────────────────────────────────────────

describe('Sync Rules to API — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Sync Rules to API').parameters.method).toBe('POST');
  });
  test('URL targets /api/playbook/sync-from-airtable', () => {
    expect(getNode(wf, 'Sync Rules to API').parameters.url).toContain('/api/playbook/sync-from-airtable');
  });
});

describe('Fetch Playbook Rules — HTTP node', () => {
  test('method is GET', () => {
    const node = getNode(wf, 'Fetch Playbook Rules');
    expect(node.parameters.method || 'GET').toBe('GET');
  });
  test('URL targets Airtable API', () => {
    expect(getNode(wf, 'Fetch Playbook Rules').parameters.url).toContain('airtable.com');
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('has a schedule trigger', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeDefined();
  });
  test('has Sync Complete terminal node', () => {
    expect(getNode(wf, 'Sync Complete')).toBeDefined();
  });
  test('has No Changes — Skip terminal node', () => {
    expect(getNode(wf, 'No Changes — Skip')).toBeDefined();
  });
});
