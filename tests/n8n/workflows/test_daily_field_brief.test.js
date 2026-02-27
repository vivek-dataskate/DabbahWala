'use strict';
/**
 * Tests for daily_field_brief.json  ([Field Agent] Daily Brief)
 *
 * Covers every Code node and every IF branch:
 *  IF:   Any Opportunities Created?  — TRUE ($json.created > 0) → Format Brief Summary
 *                                       FALSE ($json.created == 0) → Log No Contacts
 *  Code: Format Brief Summary        — full brief array, empty brief, missing fields,
 *                                      single entry, boundary values
 *  Code: Prepare Airtable Records    — full brief (>10 items gets sliced to 10),
 *                                      exactly 10, fewer than 10, empty brief,
 *                                      missing optional fields, field mapping
 *  Code: Log No Contacts             — always returns skipped sentinel
 *
 *  Flow: Generate → IF TRUE  → Format → Prepare Airtable → (HTTP, not unit-tested)
 *        Generate → IF FALSE → Log No Contacts
 *
 *  Cleanup: pure JS unit tests — no external state to clean up.
 */

const { runCode, evalIf, loadWorkflow, getCodeNode, getIfConditions } = require('../helpers/runtime');

let wf;
const code = {};
let ifAnyCreated;

beforeAll(() => {
  wf = loadWorkflow('daily_field_brief.json');
  // The runtime's $proxy is not callable as a function in vm.Script context
  // (Proxy apply trap requires a callable target). Rewrite $('NodeName') →
  // $node['NodeName'] which uses the Proxy's get trap that DOES work.
  // Also rewrite .first() → .item since the runtime exposes .item not .first().
  const patchDollarCalls = src =>
    src
      .replace(/\$\('([^']+)'\)/g, (_, name) => `$node['${name}']`)
      .replace(/\.first\(\)/g, '.item');
  code.formatBrief       = getCodeNode(wf, 'Format Brief Summary');
  code.prepareAirtable   = patchDollarCalls(getCodeNode(wf, 'Prepare Airtable Records'));
  code.logNoContacts     = getCodeNode(wf, 'Log No Contacts');
  ifAnyCreated           = getIfConditions(wf, 'Any Opportunities Created?');
});

afterAll(() => {
  wf = null;
});

// ─── Any Opportunities Created? — IF node ────────────────────────────────────

describe('Any Opportunities Created? — IF node', () => {
  test('TRUE branch: created=5 → proceed to Format Brief Summary', () => {
    expect(evalIf(ifAnyCreated, { created: 5 })).toBe(true);
  });

  test('TRUE branch: created=1 (boundary) → proceed to Format Brief Summary', () => {
    expect(evalIf(ifAnyCreated, { created: 1 })).toBe(true);
  });

  test('FALSE branch: created=0 → go to Log No Contacts', () => {
    expect(evalIf(ifAnyCreated, { created: 0 })).toBe(false);
  });

  test('FALSE branch: created missing (undefined) → go to Log No Contacts', () => {
    expect(evalIf(ifAnyCreated, {})).toBe(false);
  });

  test('FALSE branch: created=null → go to Log No Contacts', () => {
    expect(evalIf(ifAnyCreated, { created: null })).toBe(false);
  });

  test('TRUE branch: large value created=100', () => {
    expect(evalIf(ifAnyCreated, { created: 100 })).toBe(true);
  });

  test('FALSE branch: created=-1 does not pass gt 0 check', () => {
    expect(evalIf(ifAnyCreated, { created: -1 })).toBe(false);
  });
});

// ─── Format Brief Summary — Code node ────────────────────────────────────────

describe('Format Brief Summary — Code node', () => {
  const makeContact = (overrides = {}) => ({
    first_name: 'Alice',
    last_name: 'Smith',
    days_since_last_order: 14,
    total_orders: 8,
    lifetime_spend: 320,
    urgency_note: 'High value, lapsed',
    ...overrides,
  });

  const makeInput = (brief, created = brief.length) => ({
    inputItems: [{ brief, created }],
  });

  test('single contact → summary contains #1 rank, name, orders, spend, days, urgency', async () => {
    const brief = [makeContact({ first_name: 'Bob', last_name: 'Jones', days_since_last_order: 7, total_orders: 3, lifetime_spend: 95, urgency_note: 'Re-engage' })];
    const result = await runCode(code.formatBrief, makeInput(brief));
    const out = result[0].json;
    expect(out.summary).toContain('#1');
    expect(out.summary).toContain('Bob Jones');
    expect(out.summary).toContain('3 orders');
    expect(out.summary).toContain('$95');
    expect(out.summary).toContain('7d since last');
    expect(out.summary).toContain('Re-engage');
  });

  test('multiple contacts → ranked sequentially #1 #2 #3', async () => {
    const brief = [
      makeContact({ first_name: 'First', last_name: 'Contact' }),
      makeContact({ first_name: 'Second', last_name: 'Contact' }),
      makeContact({ first_name: 'Third', last_name: 'Contact' }),
    ];
    const result = await runCode(code.formatBrief, makeInput(brief));
    const summary = result[0].json.summary;
    expect(summary).toContain('#1');
    expect(summary).toContain('#2');
    expect(summary).toContain('#3');
    expect(summary).toContain('First Contact');
    expect(summary).toContain('Second Contact');
    expect(summary).toContain('Third Contact');
  });

  test('output shape: summary, created, count fields present', async () => {
    const brief = [makeContact()];
    const result = await runCode(code.formatBrief, makeInput(brief, 1));
    const out = result[0].json;
    expect(out).toHaveProperty('summary');
    expect(out).toHaveProperty('created', 1);
    expect(out).toHaveProperty('count', 1);
  });

  test('count equals length of brief array', async () => {
    const brief = [makeContact(), makeContact(), makeContact()];
    const result = await runCode(code.formatBrief, makeInput(brief, 3));
    expect(result[0].json.count).toBe(3);
  });

  test('created value is passed through to output', async () => {
    const brief = [makeContact()];
    const result = await runCode(code.formatBrief, makeInput(brief, 7));
    expect(result[0].json.created).toBe(7);
  });

  test('summary includes DabbahWala Field Brief header', async () => {
    const brief = [makeContact()];
    const result = await runCode(code.formatBrief, makeInput(brief));
    expect(result[0].json.summary).toContain('DabbahWala Field Brief');
  });

  test('summary includes "call opportunities created today" line', async () => {
    const brief = [makeContact()];
    const result = await runCode(code.formatBrief, makeInput(brief, 3));
    expect(result[0].json.summary).toContain('call opportunities created today');
  });

  test('summary includes Airtable reference footer', async () => {
    const brief = [makeContact()];
    const result = await runCode(code.formatBrief, makeInput(brief));
    expect(result[0].json.summary).toContain('Airtable');
  });

  test('empty brief array → summary still has header and footer, count=0', async () => {
    const result = await runCode(code.formatBrief, makeInput([], 0));
    const out = result[0].json;
    expect(out.count).toBe(0);
    expect(out.summary).toContain('DabbahWala Field Brief');
  });

  test('contact with missing first_name → name trimmed gracefully (no extra space)', async () => {
    const brief = [makeContact({ first_name: '', last_name: 'Doe' })];
    const result = await runCode(code.formatBrief, makeInput(brief));
    const summary = result[0].json.summary;
    expect(summary).toContain('Doe');
    // Should not have leading space "  Doe" - template.trim() is called
    expect(summary).not.toContain('  Doe');
  });

  test('contact with missing last_name → only first name appears', async () => {
    const brief = [makeContact({ first_name: 'Charlie', last_name: '' })];
    const result = await runCode(code.formatBrief, makeInput(brief));
    expect(result[0].json.summary).toContain('Charlie');
  });

  test('contact with missing days_since_last_order → defaults to 0', async () => {
    const brief = [makeContact({ days_since_last_order: undefined })];
    const result = await runCode(code.formatBrief, makeInput(brief));
    expect(result[0].json.summary).toContain('0d since last');
  });

  test('contact with missing total_orders → defaults to 0', async () => {
    const brief = [makeContact({ total_orders: undefined })];
    const result = await runCode(code.formatBrief, makeInput(brief));
    expect(result[0].json.summary).toContain('0 orders');
  });

  test('contact with missing lifetime_spend → defaults to 0', async () => {
    const brief = [makeContact({ lifetime_spend: undefined })];
    const result = await runCode(code.formatBrief, makeInput(brief));
    expect(result[0].json.summary).toContain('$0');
  });

  test('contact with missing urgency_note → empty string in line (no crash)', async () => {
    const brief = [makeContact({ urgency_note: undefined })];
    const result = await runCode(code.formatBrief, makeInput(brief));
    // Should not throw and should still produce a summary line
    expect(result[0].json.summary).toContain('#1');
  });

  test('10 contacts → all 10 ranked entries appear in summary', async () => {
    const brief = Array.from({ length: 10 }, (_, i) =>
      makeContact({ first_name: `Person${i + 1}`, last_name: 'X' })
    );
    const result = await runCode(code.formatBrief, makeInput(brief, 10));
    const summary = result[0].json.summary;
    for (let i = 1; i <= 10; i++) {
      expect(summary).toContain(`#${i}`);
      expect(summary).toContain(`Person${i}`);
    }
  });
});

// ─── Prepare Airtable Records — Code node ────────────────────────────────────

describe('Prepare Airtable Records — Code node', () => {
  /**
   * This node reads from $('Generate Daily Call List').first().json.brief
   * so we supply data via nodeData.
   */
  const makeCtx = (brief) => ({
    inputItems: [{}],
    nodeData: {
      'Generate Daily Call List': [{ brief }],
    },
  });

  const makeContact = (overrides = {}) => ({
    first_name: 'Ali',
    last_name: 'Hassan',
    phone: '+14161234567',
    priority: 'hot',
    opportunity_id: 42,
    suggested_script: 'Call about re-order',
    days_since_last_order: 10,
    total_orders: 5,
    ...overrides,
  });

  test('single contact → one record with correct field mapping', async () => {
    const brief = [makeContact()];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    const records = result[0].json.records;
    expect(records).toHaveLength(1);
    const fields = records[0].fields;
    expect(fields['Contact Name']).toBe('Ali Hassan');
    expect(fields['Phone']).toBe('+14161234567');
    expect(fields['Priority']).toBe('Hot');
    expect(fields['Status']).toBe('New');
    expect(fields['Postgres Opportunity ID']).toBe('42');
    expect(fields['Call Script']).toBe('Call about re-order');
    expect(fields['Days Since Last Order']).toBe(10);
    expect(fields['Total Orders']).toBe(5);
    expect(fields['Synced to Postgres']).toBe(false);
  });

  test('priority is title-cased: hot → Hot', async () => {
    const brief = [makeContact({ priority: 'hot' })];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records[0].fields['Priority']).toBe('Hot');
  });

  test('priority is title-cased: warm → Warm', async () => {
    const brief = [makeContact({ priority: 'warm' })];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records[0].fields['Priority']).toBe('Warm');
  });

  test('priority missing → defaults to "warm" title-cased as Warm', async () => {
    const brief = [makeContact({ priority: undefined })];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records[0].fields['Priority']).toBe('Warm');
  });

  test('more than 10 contacts → sliced to exactly 10 records', async () => {
    const brief = Array.from({ length: 15 }, (_, i) => makeContact({ opportunity_id: i + 1, first_name: `P${i + 1}` }));
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records).toHaveLength(10);
  });

  test('exactly 10 contacts → all 10 records returned', async () => {
    const brief = Array.from({ length: 10 }, (_, i) => makeContact({ opportunity_id: i + 1 }));
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records).toHaveLength(10);
  });

  test('fewer than 10 contacts → all returned (no padding)', async () => {
    const brief = [makeContact(), makeContact({ opportunity_id: 2 })];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records).toHaveLength(2);
  });

  test('empty brief array → zero records', async () => {
    const result = await runCode(code.prepareAirtable, makeCtx([]));
    expect(result[0].json.records).toHaveLength(0);
  });

  test('brief missing from node data → zero records (safe default)', async () => {
    const result = await runCode(code.prepareAirtable, makeCtx(undefined));
    expect(result[0].json.records).toHaveLength(0);
  });

  test('contact with missing first_name → Contact Name is trimmed (no leading space)', async () => {
    const brief = [makeContact({ first_name: '', last_name: 'Doe' })];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records[0].fields['Contact Name']).toBe('Doe');
  });

  test('contact with missing last_name → Contact Name is only first_name trimmed', async () => {
    const brief = [makeContact({ first_name: 'Ali', last_name: '' })];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records[0].fields['Contact Name']).toBe('Ali');
  });

  test('phone missing → defaults to empty string', async () => {
    const brief = [makeContact({ phone: undefined })];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records[0].fields['Phone']).toBe('');
  });

  test('opportunity_id is stored as string (not number)', async () => {
    const brief = [makeContact({ opportunity_id: 123 })];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records[0].fields['Postgres Opportunity ID']).toBe('123');
  });

  test('opportunity_id missing → stored as empty string', async () => {
    const brief = [makeContact({ opportunity_id: undefined })];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records[0].fields['Postgres Opportunity ID']).toBe('');
  });

  test('call script missing → defaults to empty string', async () => {
    const brief = [makeContact({ suggested_script: undefined })];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records[0].fields['Call Script']).toBe('');
  });

  test('days_since_last_order missing → defaults to 0', async () => {
    const brief = [makeContact({ days_since_last_order: undefined })];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records[0].fields['Days Since Last Order']).toBe(0);
  });

  test('total_orders missing → defaults to 0', async () => {
    const brief = [makeContact({ total_orders: undefined })];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records[0].fields['Total Orders']).toBe(0);
  });

  test('Synced to Postgres is always false', async () => {
    const brief = [makeContact()];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json.records[0].fields['Synced to Postgres']).toBe(false);
  });

  test('output shape: result has records array at top level', async () => {
    const brief = [makeContact()];
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    expect(result[0].json).toHaveProperty('records');
    expect(Array.isArray(result[0].json.records)).toBe(true);
  });

  test('slice keeps first 10 (by index) when given 12 contacts', async () => {
    const brief = Array.from({ length: 12 }, (_, i) =>
      makeContact({ first_name: `Person${i + 1}`, opportunity_id: i + 1 })
    );
    const result = await runCode(code.prepareAirtable, makeCtx(brief));
    const names = result[0].json.records.map(r => r.fields['Contact Name']);
    expect(names[0]).toBe('Person1 Hassan');
    expect(names[9]).toBe('Person10 Hassan');
    expect(names).toHaveLength(10);
  });
});

// ─── Log No Contacts — Code node ─────────────────────────────────────────────

describe('Log No Contacts — Code node', () => {
  test('always returns { status: "skipped", reason: "no_eligible_contacts" }', async () => {
    const result = await runCode(code.logNoContacts, { inputItems: [{}] });
    const out = result[0].json;
    expect(out.status).toBe('skipped');
    expect(out.reason).toBe('no_eligible_contacts');
  });

  test('returns exactly one item', async () => {
    const result = await runCode(code.logNoContacts, { inputItems: [{}] });
    expect(result).toHaveLength(1);
  });

  test('output is the same regardless of input data', async () => {
    const result = await runCode(code.logNoContacts, {
      inputItems: [{ created: 5, brief: [{ first_name: 'X' }] }],
    });
    expect(result[0].json.status).toBe('skipped');
    expect(result[0].json.reason).toBe('no_eligible_contacts');
  });

  test('output does not contain any contact data', async () => {
    const result = await runCode(code.logNoContacts, { inputItems: [{}] });
    const out = result[0].json;
    expect(out).not.toHaveProperty('brief');
    expect(out).not.toHaveProperty('created');
    expect(out).not.toHaveProperty('records');
  });
});

// ─── End-to-end flow — opportunities exist → TRUE path ───────────────────────

describe('Flow: opportunities created → IF TRUE → Format → Prepare Airtable', () => {
  const makeContact = (overrides = {}) => ({
    first_name: 'Jane',
    last_name: 'Doe',
    phone: '+14169990000',
    priority: 'warm',
    opportunity_id: 1,
    suggested_script: 'Check in about next order',
    days_since_last_order: 21,
    total_orders: 12,
    lifetime_spend: 480,
    urgency_note: 'Good candidate',
    ...overrides,
  });

  test('created=3 → IF routes TRUE → format has count=3 → prepare has 3 records', async () => {
    const brief = [makeContact(), makeContact({ opportunity_id: 2, first_name: 'Mark' }), makeContact({ opportunity_id: 3, first_name: 'Sara' })];
    const apiResponse = { brief, created: 3 };

    // Step 1: IF routes correctly
    expect(evalIf(ifAnyCreated, apiResponse)).toBe(true);

    // Step 2: Format Brief Summary
    const formatResult = await runCode(code.formatBrief, { inputItems: [apiResponse] });
    const formatted = formatResult[0].json;
    expect(formatted.count).toBe(3);
    expect(formatted.created).toBe(3);
    expect(formatted.summary).toContain('#1');
    expect(formatted.summary).toContain('#3');

    // Step 3: Prepare Airtable Records (reads from nodeData, not input)
    const prepareResult = await runCode(code.prepareAirtable, {
      inputItems: [formatted],
      nodeData: { 'Generate Daily Call List': [apiResponse] },
    });
    expect(prepareResult[0].json.records).toHaveLength(3);
  });

  test('11 contacts → format shows all 11 → airtable capped at 10', async () => {
    const brief = Array.from({ length: 11 }, (_, i) =>
      makeContact({ first_name: `P${i + 1}`, opportunity_id: i + 1 })
    );
    const apiResponse = { brief, created: 11 };

    expect(evalIf(ifAnyCreated, apiResponse)).toBe(true);

    const formatResult = await runCode(code.formatBrief, { inputItems: [apiResponse] });
    expect(formatResult[0].json.count).toBe(11);

    const prepareResult = await runCode(code.prepareAirtable, {
      inputItems: [formatResult[0].json],
      nodeData: { 'Generate Daily Call List': [apiResponse] },
    });
    expect(prepareResult[0].json.records).toHaveLength(10);
  });

  test('single contact full pipeline → summary has #1, airtable has 1 record', async () => {
    const brief = [makeContact({ first_name: 'Solo', opportunity_id: 99 })];
    const apiResponse = { brief, created: 1 };

    expect(evalIf(ifAnyCreated, apiResponse)).toBe(true);

    const formatResult = await runCode(code.formatBrief, { inputItems: [apiResponse] });
    expect(formatResult[0].json.summary).toContain('Solo');

    const prepareResult = await runCode(code.prepareAirtable, {
      inputItems: [formatResult[0].json],
      nodeData: { 'Generate Daily Call List': [apiResponse] },
    });
    expect(prepareResult[0].json.records).toHaveLength(1);
    expect(prepareResult[0].json.records[0].fields['Postgres Opportunity ID']).toBe('99');
  });
});

// ─── End-to-end flow — no contacts → FALSE path ──────────────────────────────

describe('Flow: no opportunities → IF FALSE → Log No Contacts', () => {
  test('created=0 → IF routes FALSE → Log returns skipped', async () => {
    const apiResponse = { brief: [], created: 0 };

    // IF routes to FALSE branch
    expect(evalIf(ifAnyCreated, apiResponse)).toBe(false);

    // Log No Contacts runs
    const logResult = await runCode(code.logNoContacts, { inputItems: [apiResponse] });
    expect(logResult[0].json.status).toBe('skipped');
    expect(logResult[0].json.reason).toBe('no_eligible_contacts');
  });

  test('undefined created → IF routes FALSE → Log returns skipped', async () => {
    const apiResponse = { brief: [] };
    expect(evalIf(ifAnyCreated, apiResponse)).toBe(false);

    const logResult = await runCode(code.logNoContacts, { inputItems: [apiResponse] });
    expect(logResult[0].json.status).toBe('skipped');
  });
});
