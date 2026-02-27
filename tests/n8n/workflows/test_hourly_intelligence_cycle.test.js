'use strict';
/**
 * Tests for hourly_intelligence_cycle.json  ([Intelligence] Contact Sweep)
 *
 * Covers every Code node, every IF branch, and every Switch output:
 *  IF:     Has Pending Actions?        — TRUE (total_pending > 0) → Split
 *                                        FALSE (total_pending == 0) → skip
 *  Code:   Split Into Individual Actions — sms_to_send, sales_calls, emails_to_send, mixed,
 *                                          all empty, partial presence
 *  Switch: Route by Action Type        — output 0 (send_sms), output 1 (field_sales_call),
 *                                        fallback (other _action_type values)
 *
 *  Cleanup: pure JS unit tests — no external state to clean up.
 */

const { runCode, evalIf, evalSwitch, loadWorkflow, getCodeNode, getIfConditions, getSwitchRules } = require('../helpers/runtime');

let wf;
const code = {};
let ifHasActions;
let switchRules;

beforeAll(() => {
  wf = loadWorkflow('hourly_intelligence_cycle.json');
  // The runtime's $proxy is not callable as a function in vm.Script context
  // (Proxy apply trap requires a callable target). Rewrite $('NodeName') →
  // $node['NodeName'] which uses the Proxy's get trap that DOES work.
  // Also rewrite .first() → .item since the runtime exposes .item not .first().
  const patchDollarCalls = src =>
    src
      .replace(/\$\('([^']+)'\)/g, (_, name) => `$node['${name}']`)
      .replace(/\.first\(\)/g, '.item');
  code.splitActions = patchDollarCalls(getCodeNode(wf, 'Split Into Individual Actions'));
  ifHasActions      = getIfConditions(wf, 'Has Pending Actions?');
  switchRules       = getSwitchRules(wf, 'Route by Action Type');
});

afterAll(() => {
  wf = null;
});

// ─── Has Pending Actions? — IF node ──────────────────────────────────────────

describe('Has Pending Actions? — IF node', () => {
  test('TRUE branch: total_pending=5 → proceed to split', () => {
    expect(evalIf(ifHasActions, { summary: { total_pending: 5 } })).toBe(true);
  });

  test('TRUE branch: total_pending=1 → proceed to split', () => {
    expect(evalIf(ifHasActions, { summary: { total_pending: 1 } })).toBe(true);
  });

  test('FALSE branch: total_pending=0 → skip', () => {
    expect(evalIf(ifHasActions, { summary: { total_pending: 0 } })).toBe(false);
  });

  test('FALSE branch: summary missing entirely → skip (defaults to 0)', () => {
    expect(evalIf(ifHasActions, {})).toBe(false);
  });

  test('FALSE branch: summary.total_pending missing → skip', () => {
    expect(evalIf(ifHasActions, { summary: {} })).toBe(false);
  });

  test('TRUE branch: large value total_pending=500', () => {
    expect(evalIf(ifHasActions, { summary: { total_pending: 500 } })).toBe(true);
  });
});

// ─── Split Into Individual Actions — Code node ────────────────────────────────

describe('Split Into Individual Actions — Code node', () => {
  /**
   * The code reads from $('Get Pending Actions').first().json, so we
   * supply the data via nodeData['Get Pending Actions'].
   */
  const makeCtx = (pendingData) => ({
    inputItems: [{}],
    nodeData: {
      'Get Pending Actions': [pendingData],
    },
  });

  // ── sms_to_send ──────────────────────────────────────────────────────────────

  test('one SMS item → single item with _action_type=send_sms', async () => {
    const ctx = makeCtx({
      sms_to_send: [{ id: 1, contact_id: 'c1', phone: '+14161111111' }],
      sales_calls: [],
      emails_to_send: [],
    });
    const result = await runCode(code.splitActions, ctx);
    expect(result).toHaveLength(1);
    expect(result[0].json._action_type).toBe('send_sms');
    expect(result[0].json.contact_id).toBe('c1');
  });

  test('multiple SMS items → all tagged send_sms', async () => {
    const ctx = makeCtx({
      sms_to_send: [
        { id: 1, contact_id: 'c1' },
        { id: 2, contact_id: 'c2' },
        { id: 3, contact_id: 'c3' },
      ],
    });
    const result = await runCode(code.splitActions, ctx);
    const smsItems = result.filter(r => r.json._action_type === 'send_sms');
    expect(smsItems).toHaveLength(3);
    expect(smsItems.map(r => r.json.contact_id)).toEqual(['c1', 'c2', 'c3']);
  });

  // ── sales_calls ──────────────────────────────────────────────────────────────

  test('one sales call → single item with _action_type=field_sales_call', async () => {
    const ctx = makeCtx({
      sales_calls: [{ id: 10, contact_id: 'd1', priority: 'hot' }],
    });
    const result = await runCode(code.splitActions, ctx);
    expect(result).toHaveLength(1);
    expect(result[0].json._action_type).toBe('field_sales_call');
    expect(result[0].json.priority).toBe('hot');
  });

  test('multiple sales calls → all tagged field_sales_call', async () => {
    const ctx = makeCtx({
      sales_calls: [
        { id: 10, contact_id: 'd1' },
        { id: 11, contact_id: 'd2' },
      ],
    });
    const result = await runCode(code.splitActions, ctx);
    const callItems = result.filter(r => r.json._action_type === 'field_sales_call');
    expect(callItems).toHaveLength(2);
  });

  // ── emails_to_send ───────────────────────────────────────────────────────────

  test('one email item → single item with _action_type=send_email', async () => {
    const ctx = makeCtx({
      emails_to_send: [{ id: 20, contact_id: 'e1', subject: 'Test' }],
    });
    const result = await runCode(code.splitActions, ctx);
    expect(result).toHaveLength(1);
    expect(result[0].json._action_type).toBe('send_email');
    expect(result[0].json.contact_id).toBe('e1');
  });

  test('multiple email items → all tagged send_email', async () => {
    const ctx = makeCtx({
      emails_to_send: [
        { id: 20, contact_id: 'e1' },
        { id: 21, contact_id: 'e2' },
        { id: 22, contact_id: 'e3' },
      ],
    });
    const result = await runCode(code.splitActions, ctx);
    const emailItems = result.filter(r => r.json._action_type === 'send_email');
    expect(emailItems).toHaveLength(3);
  });

  // ── mixed arrays ─────────────────────────────────────────────────────────────

  test('mixed: 2 SMS + 1 call + 1 email → 4 items with correct types', async () => {
    const ctx = makeCtx({
      sms_to_send:    [{ id: 1 }, { id: 2 }],
      sales_calls:    [{ id: 10 }],
      emails_to_send: [{ id: 20 }],
    });
    const result = await runCode(code.splitActions, ctx);
    expect(result).toHaveLength(4);
    const types = result.map(r => r.json._action_type);
    expect(types.filter(t => t === 'send_sms')).toHaveLength(2);
    expect(types.filter(t => t === 'field_sales_call')).toHaveLength(1);
    expect(types.filter(t => t === 'send_email')).toHaveLength(1);
  });

  test('SMS items come before sales_calls items in output order', async () => {
    const ctx = makeCtx({
      sms_to_send:  [{ id: 1, contact_id: 'first' }],
      sales_calls:  [{ id: 2, contact_id: 'second' }],
    });
    const result = await runCode(code.splitActions, ctx);
    expect(result[0].json._action_type).toBe('send_sms');
    expect(result[1].json._action_type).toBe('field_sales_call');
  });

  // ── all empty arrays ─────────────────────────────────────────────────────────

  test('all arrays empty → returns sentinel item with _action_type=none', async () => {
    const ctx = makeCtx({
      sms_to_send:    [],
      sales_calls:    [],
      emails_to_send: [],
    });
    const result = await runCode(code.splitActions, ctx);
    expect(result).toHaveLength(1);
    expect(result[0].json._action_type).toBe('none');
  });

  test('all arrays missing → returns sentinel item with _action_type=none', async () => {
    const ctx = makeCtx({});
    const result = await runCode(code.splitActions, ctx);
    expect(result).toHaveLength(1);
    expect(result[0].json._action_type).toBe('none');
  });

  test('empty object from Get Pending Actions → returns sentinel none item', async () => {
    // API always returns at least an object; passing {} simulates a response with no arrays
    const ctx = makeCtx({});
    const result = await runCode(code.splitActions, ctx);
    expect(result).toHaveLength(1);
    expect(result[0].json._action_type).toBe('none');
  });

  // ── extra fields are preserved ───────────────────────────────────────────────

  test('all original fields are spread onto each action item', async () => {
    const ctx = makeCtx({
      sms_to_send: [{
        id: 99,
        contact_id: 'c-spread',
        phone: '+14169999999',
        suggested_message: 'Hello!',
        priority: 'hot',
      }],
    });
    const result = await runCode(code.splitActions, ctx);
    const item = result[0].json;
    expect(item._action_type).toBe('send_sms');
    expect(item.id).toBe(99);
    expect(item.contact_id).toBe('c-spread');
    expect(item.phone).toBe('+14169999999');
    expect(item.suggested_message).toBe('Hello!');
    expect(item.priority).toBe('hot');
  });
});

// ─── Route by Action Type — Switch node ──────────────────────────────────────

describe('Route by Action Type — Switch node', () => {
  test('_action_type=send_sms → output index 0 (SMS branch)', () => {
    const idx = evalSwitch(switchRules, { _action_type: 'send_sms' });
    expect(idx).toBe('SMS');
  });

  test('_action_type=field_sales_call → output index 1 (Sales Call branch)', () => {
    const idx = evalSwitch(switchRules, { _action_type: 'field_sales_call' });
    expect(idx).toBe('Sales Call');
  });

  test('_action_type=send_email → no match → fallback (-1)', () => {
    const idx = evalSwitch(switchRules, { _action_type: 'send_email' });
    expect(idx).toBe('fallback');
  });

  test('_action_type=none → no match → fallback (-1)', () => {
    const idx = evalSwitch(switchRules, { _action_type: 'none' });
    expect(idx).toBe('fallback');
  });

  test('_action_type=escalate → no match → fallback (-1)', () => {
    const idx = evalSwitch(switchRules, { _action_type: 'escalate' });
    expect(idx).toBe('fallback');
  });

  test('_action_type missing → no match → fallback (-1)', () => {
    const idx = evalSwitch(switchRules, {});
    expect(idx).toBe('fallback');
  });

  test('_action_type case sensitive: "Send_SMS" does NOT match send_sms branch', () => {
    const idx = evalSwitch(switchRules, { _action_type: 'Send_SMS' });
    expect(idx).not.toBe('SMS');
  });
});

// ─── End-to-end flow — pending actions exist ─────────────────────────────────

describe('Flow: pending actions → IF TRUE → Split → Switch routing', () => {
  const makeCtx = (pendingData) => ({
    inputItems: [{}],
    nodeData: { 'Get Pending Actions': [pendingData] },
  });

  test('total_pending=3 → IF routes TRUE → split produces 3 items', async () => {
    expect(evalIf(ifHasActions, { summary: { total_pending: 3 } })).toBe(true);

    const ctx = makeCtx({
      sms_to_send:  [{ id: 1 }, { id: 2 }],
      sales_calls:  [{ id: 10 }],
    });
    const result = await runCode(code.splitActions, ctx);
    expect(result).toHaveLength(3);
  });

  test('SMS action → split tags it → switch routes to SMS branch (index 0)', async () => {
    const ctx = makeCtx({ sms_to_send: [{ id: 5, phone: '+1111111111' }] });
    const items = await runCode(code.splitActions, ctx);
    const idx = evalSwitch(switchRules, items[0].json);
    expect(idx).toBe('SMS');
  });

  test('sales call action → split tags it → switch routes to Sales Call branch (index 1)', async () => {
    const ctx = makeCtx({ sales_calls: [{ id: 15, priority: 'warm' }] });
    const items = await runCode(code.splitActions, ctx);
    const idx = evalSwitch(switchRules, items[0].json);
    expect(idx).toBe('Sales Call');
  });

  test('email action → split tags it → switch falls through to fallback (-1)', async () => {
    const ctx = makeCtx({ emails_to_send: [{ id: 25, subject: 'Re-engage' }] });
    const items = await runCode(code.splitActions, ctx);
    const idx = evalSwitch(switchRules, items[0].json);
    expect(idx).toBe('fallback');
  });
});

describe('Flow: no pending actions → IF FALSE → skip', () => {
  test('total_pending=0 → IF evaluates false', () => {
    expect(evalIf(ifHasActions, { summary: { total_pending: 0 } })).toBe(false);
  });

  test('summary undefined → IF evaluates false', () => {
    expect(evalIf(ifHasActions, {})).toBe(false);
  });
});
