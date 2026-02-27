'use strict';
/**
 * Tests for sms_dispatch.json  ([SMS] Dispatch Queue)
 *
 * Covers every Code node and every IF branch:
 *  IF:   Has Pending SMS?        — TRUE ($json.length > 0) → Split Contacts
 *                                  FALSE ($json.length == 0) → No Pending — Skip
 *  Code: Split Contacts          — array input, empty array, non-array input
 *  Code: Build SMS Message       — lifecycle='new_customer', sms_level>=3, sms_level>=2,
 *                                  default (sms_level=1), missing fields
 *
 *  Flow: [Fetch] → IF TRUE  → Split → Build → (Telnyx HTTP, not unit-tested)
 *        [Fetch] → IF FALSE → Skip
 *
 *  Cleanup: pure JS unit tests — no external state to clean up.
 */

const { runCode, evalIf, loadWorkflow, getCodeNode, getIfConditions } = require('../helpers/runtime');

let wf;
const code = {};
let ifHasPending;

beforeAll(() => {
  wf = loadWorkflow('sms_dispatch.json');
  code.splitContacts  = getCodeNode(wf, 'Split Contacts');
  code.buildMessage   = getCodeNode(wf, 'Build SMS Message');
  ifHasPending        = getIfConditions(wf, 'Has Pending SMS?');
});

afterAll(() => {
  wf = null;
});

// ─── Has Pending SMS? — IF node ───────────────────────────────────────────────

describe('Has Pending SMS? — IF node', () => {
  test('TRUE branch: length=3 (non-empty list) → proceed to Split Contacts', () => {
    expect(evalIf(ifHasPending, { length: 3 })).toBe(true);
  });

  test('TRUE branch: length=1 (single item) → proceed to Split Contacts', () => {
    expect(evalIf(ifHasPending, { length: 1 })).toBe(true);
  });

  test('FALSE branch: length=0 (empty list) → skip', () => {
    expect(evalIf(ifHasPending, { length: 0 })).toBe(false);
  });

  test('FALSE branch: length missing (undefined) → skip', () => {
    expect(evalIf(ifHasPending, {})).toBe(false);
  });

  test('TRUE branch: large list length=100', () => {
    expect(evalIf(ifHasPending, { length: 100 })).toBe(true);
  });
});

// ─── Split Contacts — Code node ───────────────────────────────────────────────

describe('Split Contacts — Code node', () => {
  const makeContacts = (n) =>
    Array.from({ length: n }, (_, i) => ({
      contact_id: `c-${i + 1}`,
      phone: `+1416000000${i}`,
      contact_email: `user${i}@example.com`,
      sms_level: 1,
      lifecycle: 'cold',
    }));

  test('array of 3 contacts → returns 3 individual items', async () => {
    const contacts = makeContacts(3);
    const result = await runCode(code.splitContacts, { inputItems: [contacts] });
    expect(result).toHaveLength(3);
    expect(result[0].json.contact_id).toBe('c-1');
    expect(result[1].json.contact_id).toBe('c-2');
    expect(result[2].json.contact_id).toBe('c-3');
  });

  test('single contact in array → returns 1 item', async () => {
    const contacts = makeContacts(1);
    const result = await runCode(code.splitContacts, { inputItems: [contacts] });
    expect(result).toHaveLength(1);
    expect(result[0].json.phone).toBe('+14160000000');
  });

  test('empty array → returns empty result', async () => {
    const result = await runCode(code.splitContacts, { inputItems: [[]] });
    // Code returns [] for empty, runtime normalises to [{json:{}}] or empty
    // The code returns [] so runtime wraps it — length may be 0 or 1 depending on normalisation
    // Verify no contact_id fields (i.e. no real contacts were produced)
    const hasRealContact = result.some(r => r.json && r.json.contact_id);
    expect(hasRealContact).toBe(false);
  });

  test('non-array input (object, not array) → returns empty', async () => {
    // Pass an object (not an array) as the first item — the code checks Array.isArray()
    const result = await runCode(code.splitContacts, { inputItems: [{ not: 'an-array' }] });
    const hasRealContact = result.some(r => r.json && r.json.contact_id);
    expect(hasRealContact).toBe(false);
  });

  test('preserves all contact fields in each split item', async () => {
    const contacts = [{
      contact_id: 'c-99',
      phone: '+14161234567',
      contact_email: 'test@example.com',
      sms_level: 2,
      lifecycle: 'warm',
    }];
    const result = await runCode(code.splitContacts, { inputItems: [contacts] });
    expect(result[0].json.sms_level).toBe(2);
    expect(result[0].json.lifecycle).toBe('warm');
    expect(result[0].json.contact_email).toBe('test@example.com');
  });

  test('array of 10 contacts → returns exactly 10 items', async () => {
    const contacts = makeContacts(10);
    const result = await runCode(code.splitContacts, { inputItems: [contacts] });
    expect(result).toHaveLength(10);
  });
});

// ─── Build SMS Message — Code node ───────────────────────────────────────────

describe('Build SMS Message — Code node', () => {
  const makeContact = (overrides = {}) => ({
    contact_id: 'c-42',
    phone: '+14161234567',
    contact_email: 'john.doe@example.com',
    sms_level: 1,
    lifecycle: 'cold',
    ...overrides,
  });

  // ── lifecycle = 'new_customer' branch ────────────────────────────────────────

  test('lifecycle=new_customer → welcome message with email username', async () => {
    const contact = makeContact({ lifecycle: 'new_customer', contact_email: 'alice@example.com' });
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    const out = result[0].json;
    expect(out.message_body).toContain('alice');
    expect(out.message_body).toContain('Thanks for your recent order');
    expect(out.message_body).toContain('DabbahWala');
    expect(out.message_body).toContain('Reply YES');
  });

  test('lifecycle=new_customer → email username extracted before @ symbol', async () => {
    const contact = makeContact({ lifecycle: 'new_customer', contact_email: 'bob.smith@mail.com' });
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    expect(result[0].json.message_body).toContain('bob.smith');
    expect(result[0].json.message_body).not.toContain('@mail.com');
  });

  // ── sms_level >= 3 branch ────────────────────────────────────────────────────

  test('sms_level=3 → reactivation "We miss you" message', async () => {
    const contact = makeContact({ sms_level: 3, lifecycle: 'cold' });
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    const msg = result[0].json.message_body;
    expect(msg).toContain('miss you');
    expect(msg).toContain('DabbahWala');
  });

  test('sms_level=5 (high level) → reactivation message (>= 3 takes priority)', async () => {
    const contact = makeContact({ sms_level: 5, lifecycle: 'cold' });
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    expect(result[0].json.message_body).toContain('miss you');
  });

  test('sms_level=3, lifecycle=warm → reactivation message (lifecycle=new_customer only overrides)', async () => {
    const contact = makeContact({ sms_level: 3, lifecycle: 'warm' });
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    expect(result[0].json.message_body).toContain('miss you');
  });

  // ── sms_level >= 2 branch ────────────────────────────────────────────────────

  test('sms_level=2 → special offer message', async () => {
    const contact = makeContact({ sms_level: 2, lifecycle: 'cold' });
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    const msg = result[0].json.message_body;
    expect(msg).toContain('Special offer');
    expect(msg).toContain('DabbahWala');
    expect(msg).toContain('Reply STOP');
  });

  test('sms_level=2, lifecycle=lapsed → special offer (not new_customer override)', async () => {
    const contact = makeContact({ sms_level: 2, lifecycle: 'lapsed' });
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    expect(result[0].json.message_body).toContain('Special offer');
  });

  // ── default sms_level=1 branch ───────────────────────────────────────────────

  test('sms_level=1 → fresh items message', async () => {
    const contact = makeContact({ sms_level: 1, lifecycle: 'cold' });
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    const msg = result[0].json.message_body;
    expect(msg).toContain('Fresh items');
    expect(msg).toContain('DabbahWala');
    expect(msg).toContain('Reply STOP');
  });

  test('sms_level missing → defaults to 1 → fresh items message', async () => {
    const contact = makeContact();
    delete contact.sms_level;
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    expect(result[0].json.message_body).toContain('Fresh items');
    expect(result[0].json.sms_level).toBe(1);
  });

  test('lifecycle missing → defaults to cold → uses sms_level routing', async () => {
    const contact = makeContact({ sms_level: 2 });
    delete contact.lifecycle;
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    expect(result[0].json.message_body).toContain('Special offer');
  });

  // ── Output shape ─────────────────────────────────────────────────────────────

  test('output contains all required fields: contact_id, phone, email, message_body, sms_level', async () => {
    const contact = makeContact({ sms_level: 1 });
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    const out = result[0].json;
    expect(out).toHaveProperty('contact_id', 'c-42');
    expect(out).toHaveProperty('phone', '+14161234567');
    expect(out).toHaveProperty('email', 'john.doe@example.com');
    expect(out).toHaveProperty('message_body');
    expect(out).toHaveProperty('sms_level', 1);
  });

  test('sms_level is preserved in output for sms_level=3', async () => {
    const contact = makeContact({ sms_level: 3 });
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    expect(result[0].json.sms_level).toBe(3);
  });

  test('contact_id and phone are passed through unchanged', async () => {
    const contact = makeContact({ contact_id: 'xyz-999', phone: '+19995550123', sms_level: 2 });
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    const out = result[0].json;
    expect(out.contact_id).toBe('xyz-999');
    expect(out.phone).toBe('+19995550123');
  });

  test('email field in output = contact_email from input', async () => {
    const contact = makeContact({ contact_email: 'test.user@domain.org' });
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    expect(result[0].json.email).toBe('test.user@domain.org');
  });

  // ── Edge cases ───────────────────────────────────────────────────────────────

  test('lifecycle=new_customer with sms_level=3 → welcome message wins (lifecycle checked first)', async () => {
    const contact = makeContact({ lifecycle: 'new_customer', sms_level: 3, contact_email: 'new@test.com' });
    const result = await runCode(code.buildMessage, { inputItems: [contact] });
    const msg = result[0].json.message_body;
    expect(msg).toContain('new');           // username from email
    expect(msg).toContain('Thanks for your recent order');
    expect(msg).not.toContain('miss you');  // sms_level>=3 branch NOT reached
  });
});

// ─── End-to-end flow — IF TRUE path ──────────────────────────────────────────

describe('Flow: non-empty contacts → IF TRUE → Split → Build', () => {
  test('2-contact array splits and each builds correct message', async () => {
    const contacts = [
      { contact_id: 'a1', phone: '+14161111111', contact_email: 'new@ex.com', sms_level: 1, lifecycle: 'new_customer' },
      { contact_id: 'a2', phone: '+14162222222', contact_email: 'lapsed@ex.com', sms_level: 3, lifecycle: 'cold' },
    ];

    // IF routes correctly
    expect(evalIf(ifHasPending, { length: contacts.length })).toBe(true);

    // Split
    const split = await runCode(code.splitContacts, { inputItems: [contacts] });
    expect(split).toHaveLength(2);

    // Build for contact 1 (new_customer)
    const msg1 = await runCode(code.buildMessage, { inputItems: [split[0].json] });
    expect(msg1[0].json.message_body).toContain('Thanks for your recent order');

    // Build for contact 2 (sms_level=3)
    const msg2 = await runCode(code.buildMessage, { inputItems: [split[1].json] });
    expect(msg2[0].json.message_body).toContain('miss you');
  });
});

describe('Flow: empty list → IF FALSE → skip', () => {
  test('length=0 → IF evaluates false (no split, no messages)', () => {
    expect(evalIf(ifHasPending, { length: 0 })).toBe(false);
  });
});
