'use strict';
/**
 * Tests for [Field Agent] Outcome Sync — airtable_outcome_sync.json
 *
 * Covers:
 *  Code: Map Airtable Status → API Status, Flatten Records
 *  IF:   Has Final Outcome?
 */

const { runCode, evalIf, loadWorkflow, getCodeNode, getIfConditions } = require('../helpers/runtime');

let wf;
const code = {};

beforeAll(() => {
  wf = loadWorkflow('airtable_outcome_sync.json');
  code.mapStatus   = getCodeNode(wf, 'Map Airtable Status → API Status');
  code.flatten     = getCodeNode(wf, 'Flatten Records');
  code.ifOutcome   = getIfConditions(wf, 'Has Final Outcome?');
});

// ─── Map Airtable Status → API Status ────────────────────────────────────────

describe('Map Airtable Status → API Status — Code node', () => {
  const makeItem = (status, opportunityId = 42, outcomeNotes = '') => ({
    id: 'rec-123',
    fields: { Status: status, 'Postgres Opportunity ID': opportunityId, 'Outcome Notes': outcomeNotes },
  });

  const STATUS_CASES = [
    // [airtableStatus, expectedApiStatus, expectedOutcome]
    ['Ordered',         'completed',   'ordered'],
    ['ordered',         'completed',   'ordered'],
    ['Converted',       'completed',   'ordered'],
    ['Not Interested',  'completed',   'not_interested'],
    ['not_interested',  'completed',   'not_interested'],
    ['Declined',        'completed',   'not_interested'],
    ['No Answer',       'completed',   'no_answer'],
    ['no_answer',       'completed',   'no_answer'],
    ['Unreachable',     'completed',   'no_answer'],
    ['In Progress',     'dispatched',  null],
    ['in_progress',     'dispatched',  null],
    ['Contacted',       'dispatched',  null],
  ];

  test.each(STATUS_CASES)('"%s" → apiStatus="%s", outcome="%s"', async (status, expectedApi, expectedOutcome) => {
    const item = makeItem(status, 99, 'some notes');
    const result = await runCode(code.mapStatus, { inputItems: [item] });
    const out = result[0]?.json || result.json;
    expect(out.api_status).toBe(expectedApi);
    expect(out.outcome).toBe(expectedOutcome);
    expect(out.opportunity_id).toBe(99);
    expect(out.airtable_record_id).toBe('rec-123');
  });

  test('unknown status falls to default (completed + status as outcome)', async () => {
    const item = makeItem('Custom Status', 50);
    const result = await runCode(code.mapStatus, { inputItems: [item] });
    const out = result[0]?.json || result.json;
    expect(out.api_status).toBe('completed');
    expect(out.outcome).toBe('custom status');
  });

  test('null/undefined status falls to default', async () => {
    const item = makeItem(undefined, 51);
    const result = await runCode(code.mapStatus, { inputItems: [item] });
    const out = result[0]?.json || result.json;
    expect(out.api_status).toBe('completed');
    // outcome = undefined?.toLowerCase() || 'no_answer' → 'no_answer'
    expect(out.outcome).toBe('no_answer');
  });

  test('outcome_notes are included in output', async () => {
    const item = makeItem('Ordered', 10, 'Customer said they loved it');
    const result = await runCode(code.mapStatus, { inputItems: [item] });
    const out = result[0]?.json || result.json;
    expect(out.outcome_notes).toBe('Customer said they loved it');
  });

  test('outcome_notes defaults to empty string when missing', async () => {
    const item = { id: 'rec-456', fields: { Status: 'Ordered', 'Postgres Opportunity ID': 11 } };
    const result = await runCode(code.mapStatus, { inputItems: [item] });
    const out = result[0]?.json || result.json;
    expect(out.outcome_notes).toBe('');
  });
});

// ─── Flatten Records ──────────────────────────────────────────────────────────

describe('Flatten Records — Code node', () => {
  test('maps Airtable records to flat json objects', async () => {
    const data = {
      records: [
        { id: 'recA', fields: { Status: 'New', 'Postgres Opportunity ID': 1 } },
        { id: 'recB', fields: { Status: 'Ordered', 'Postgres Opportunity ID': 2 } },
      ]
    };
    const result = await runCode(code.flatten, { inputItems: [data] });
    expect(result).toHaveLength(2);
    expect(result[0].json.airtable_record_id).toBe('recA');
    expect(result[0].json.Status).toBe('New');
    expect(result[1].json.airtable_record_id).toBe('recB');
  });

  test('returns empty array when records is empty', async () => {
    const result = await runCode(code.flatten, { inputItems: [{ records: [] }] });
    expect(result).toHaveLength(0);
  });

  test('returns empty array when records field is missing', async () => {
    const result = await runCode(code.flatten, { inputItems: [{}] });
    expect(result).toHaveLength(0);
  });
});

// ─── Has Final Outcome? — IF node ─────────────────────────────────────────────

describe('Has Final Outcome? — IF node', () => {
  test('TRUE: outcome = "ordered"', () => {
    expect(evalIf(code.ifOutcome, { outcome: 'ordered' })).toBe(true);
  });
  test('TRUE: outcome = "not_interested"', () => {
    expect(evalIf(code.ifOutcome, { outcome: 'not_interested' })).toBe(true);
  });
  test('FALSE: outcome = null (in_progress/contacted)', () => {
    expect(evalIf(code.ifOutcome, { outcome: null })).toBe(false);
  });
  test('FALSE: outcome is empty string', () => {
    expect(evalIf(code.ifOutcome, { outcome: '' })).toBe(false);
  });
  test('FALSE: outcome field missing', () => {
    expect(evalIf(code.ifOutcome, {})).toBe(false);
  });
});

// ─── Flow tests ───────────────────────────────────────────────────────────────

describe('Flow: Ordered record → outcome posted', () => {
  test('map status → has outcome TRUE', async () => {
    // mapStatus receives the raw Airtable record (id + fields) directly
    const rawRecord = { id: 'recX', fields: { Status: 'Ordered', 'Postgres Opportunity ID': 77, 'Outcome Notes': 'Great!' } };
    const mapped = await runCode(code.mapStatus, { inputItems: [rawRecord] });
    const out = mapped[0]?.json || mapped.json;
    expect(out.api_status).toBe('completed');
    expect(out.outcome).toBe('ordered');
    expect(evalIf(code.ifOutcome, out)).toBe(true);  // → POST outcome
  });
});

describe('Flow: In Progress record → no outcome posted', () => {
  test('map status → has outcome FALSE', async () => {
    const rawRecord = { id: 'recY', fields: { Status: 'In Progress', 'Postgres Opportunity ID': 78 } };
    const mapped = await runCode(code.mapStatus, { inputItems: [rawRecord] });
    const out = mapped[0]?.json || mapped.json;
    expect(out.api_status).toBe('dispatched');
    expect(out.outcome).toBeNull();
    expect(evalIf(code.ifOutcome, out)).toBe(false);  // → skip POST
  });
});
