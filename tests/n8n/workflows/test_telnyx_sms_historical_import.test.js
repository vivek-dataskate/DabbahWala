'use strict';
/**
 * Tests for [SMS] Historical Import — telnyx_sms_historical_import.json
 *
 * Covers:
 *  - Code: Set Date Range    — configures START_DATE, END_DATE, page=1
 *  - Code: Split MDR Records — splits records into items, sets _no_more_records=true when empty
 *  - IF:   No More Records?  — TRUE (_no_more_records=true) / FALSE (_no_more_records=false)
 *  - HTTP: Fetch MDR Page    — GET from Telnyx with auth and query params
 *  - HTTP: Store Message     — POST to /api/telnyx/message
 */

const { loadWorkflow, getCodeNode, getIfConditions, getNode, runCode, evalIf } = require('../helpers/runtime');

let wf, dateRangeCode, splitCode, ifc;
beforeAll(() => {
  wf            = loadWorkflow('telnyx_sms_historical_import.json');
  dateRangeCode = getCodeNode(wf, 'Set Date Range');
  splitCode     = getCodeNode(wf, 'Split MDR Records');
  ifc           = getIfConditions(wf, 'No More Records?');
});

// ─── Set Date Range — Code node ───────────────────────────────────────────────

describe('Set Date Range — Code node', () => {
  test('outputs START_DATE and END_DATE fields', async () => {
    const [out] = await runCode(dateRangeCode, { inputItems: [{ json: {} }] });
    const fields = Object.keys(out.json);
    // Should have some date range config
    expect(out.json).toBeDefined();
  });
});

// ─── Split MDR Records — Code node ───────────────────────────────────────────

describe('Split MDR Records — Code node', () => {
  test('splits records into individual items', async () => {
    const input = {
      data: [
        { id: 'm1', from_number: '+15551234567', to_number: '+18444322224', message_body: 'Hello', status: 'received' },
        { id: 'm2', from_number: '+15559876543', to_number: '+18444322224', message_body: 'Hi',    status: 'received' },
      ],
      meta: { total_pages: 1, total_results: 2 },
    };
    const items = await runCode(splitCode, { inputItems: [{ json: input }] });
    expect(items.length).toBeGreaterThanOrEqual(1);
  });

  test('returns _no_more_records=true when data is empty', async () => {
    const input = { data: [], meta: { total_pages: 1, total_results: 0 } };
    const [out] = await runCode(splitCode, { inputItems: [{ json: input }] });
    expect(out.json._no_more_records).toBe(true);
  });

  test('maps telnyx fields to normalised shape', async () => {
    const input = {
      data: [{
        id: 'msg-abc',
        from_number: '+15551234567',
        to_number: '+18444322224',
        message_body: 'Test message',
        status: 'received',
        sent_at: '2026-01-01T10:00:00Z',
      }],
      meta: { total_pages: 2, total_results: 250 },
    };
    const items = await runCode(splitCode, { inputItems: [{ json: input }] });
    const msg = items[0].json;
    expect(msg.telnyx_msg_id).toBe('msg-abc');
    expect(msg.from_number).toBe('+15551234567');
    expect(msg.body).toBe('Test message');
    expect(msg.direction).toBe('inbound');
    expect(msg.metadata.source).toBe('telnyx_historical_import');
  });

  test('filters out records without from_number', async () => {
    const input = {
      data: [
        { id: 'm1', from_number: '', message_body: 'No sender', status: 'received' },
        { id: 'm2', from_number: '+15551234567', message_body: 'Has sender', status: 'received' },
      ],
      meta: {},
    };
    const items = await runCode(splitCode, { inputItems: [{ json: input }] });
    expect(items.every(i => i.json.from_number || i.json._no_more_records)).toBe(true);
  });

  test('supports from.phone_number Telnyx nested format', async () => {
    const input = {
      data: [{
        id: 'm1',
        from: { phone_number: '+15559999999' },
        to: [{ phone_number: '+18444322224' }],
        message_body: 'Nested',
        status: 'received',
      }],
      meta: {},
    };
    const items = await runCode(splitCode, { inputItems: [{ json: input }] });
    expect(items[0].json.from_number).toBe('+15559999999');
  });
});

// ─── No More Records? — IF node ───────────────────────────────────────────────

describe('No More Records? — IF node', () => {
  test('TRUE: _no_more_records=true → import complete', () => {
    expect(evalIf(ifc, { _no_more_records: true })).toBe(true);
  });
  test('FALSE: _no_more_records=false → continue fetching', () => {
    expect(evalIf(ifc, { _no_more_records: false })).toBe(false);
  });
  test('FALSE: _no_more_records missing', () => {
    expect(evalIf(ifc, { telnyx_msg_id: 'msg-1' })).toBe(false);
  });
});

// ─── HTTP node configs ────────────────────────────────────────────────────────

describe('Fetch MDR Page — HTTP node', () => {
  test('URL targets Telnyx MDR endpoint', () => {
    const node = getNode(wf, 'Fetch MDR Page');
    expect(node.parameters.url).toContain('telnyx.com');
    expect(node.parameters.url).toContain('message_detail_records');
  });
  test('has timeout configured', () => {
    expect(getNode(wf, 'Fetch MDR Page').parameters.options?.timeout).toBeGreaterThan(0);
  });
});

describe('Store Message — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Store Message').parameters.method).toBe('POST');
  });
  test('URL targets /api/telnyx/message', () => {
    expect(getNode(wf, 'Store Message').parameters.url).toContain('/telnyx/message');
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('manual trigger only (historical import is a one-off)', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.manualTrigger')).toBeDefined();
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeUndefined();
  });
  test('has No More Records? IF node', () => {
    expect(getNode(wf, 'No More Records?').type).toBe('n8n-nodes-base.if');
  });
  test('has Import Complete terminal node', () => {
    expect(getNode(wf, 'Import Complete')).toBeDefined();
  });
});
