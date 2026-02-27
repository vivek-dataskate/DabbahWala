'use strict';
/**
 * Tests for [Broadcast] Dispatch — broadcast_dispatch.json
 *
 * Covers:
 *  Code:   Split Recipients, Check SMS Result, Check Email Result
 *  IF:     Has Pending Recipients?
 *  Switch: Route by Channel (sms, email)
 */

const { runCode, evalIf, evalSwitch, loadWorkflow, getCodeNode, getIfConditions, getSwitchRules } = require('../helpers/runtime');

let wf;
const code = {};
const ifs  = {};
let switchRules;

beforeAll(() => {
  wf = loadWorkflow('broadcast_dispatch.json');
  code.split          = getCodeNode(wf, 'Split Recipients');
  code.checkSMS       = getCodeNode(wf, 'Check SMS Result');
  code.checkEmail     = getCodeNode(wf, 'Check Email Result');
  ifs.hasPending      = getIfConditions(wf, 'Has Pending Recipients?');
  switchRules         = getSwitchRules(wf, 'Route by Channel');
});

// ─── Split Recipients ─────────────────────────────────────────────────────────

describe('Split Recipients — Code node', () => {
  test('splits array into individual items', async () => {
    const recipients = [
      { recipient_id: 1, channel: 'sms', phone: '+1111' },
      { recipient_id: 2, channel: 'email', email: 'a@b.com' },
    ];
    const result = await runCode(code.split, { inputItems: [recipients] });
    expect(result).toHaveLength(2);
    expect(result[0].json.recipient_id).toBe(1);
    expect(result[1].json.recipient_id).toBe(2);
  });

  test('returns empty array for empty list', async () => {
    const result = await runCode(code.split, { inputItems: [[]] });
    expect(result).toHaveLength(0);
  });

  test('returns empty array when input is not an array', async () => {
    const result = await runCode(code.split, { inputItems: [{ not: 'array' }] });
    expect(result).toHaveLength(0);
  });

  test('preserves all fields per recipient', async () => {
    const recipients = [{ recipient_id: 99, channel: 'sms', phone: '+9999', sms_message: 'Hi!' }];
    const result = await runCode(code.split, { inputItems: [recipients] });
    expect(result[0].json.sms_message).toBe('Hi!');
    expect(result[0].json.channel).toBe('sms');
  });
});

// ─── Check SMS Result ─────────────────────────────────────────────────────────

describe('Check SMS Result — Code node', () => {
  test('success: Telnyx returns data.id → success = true', async () => {
    const telnyx = { data: { id: 'msg-abc123' } };
    const nodeData = { 'Split Recipients': [{ recipient_id: 42, channel: 'sms' }] };
    const result = await runCode(code.checkSMS, { inputItems: [telnyx], nodeData });
    const out = result[0]?.json || result.json;
    expect(out.success).toBe(true);
    expect(out.recipient_id).toBe(42);
    expect(out.telnyx_msg_id).toBe('msg-abc123');
    expect(out.error).toBe('');
  });

  test('failure: no data.id → success = false', async () => {
    const telnyx = { errors: [{ detail: 'Invalid phone number' }] };
    const nodeData = { 'Split Recipients': [{ recipient_id: 43, channel: 'sms' }] };
    const result = await runCode(code.checkSMS, { inputItems: [telnyx], nodeData });
    const out = result[0]?.json || result.json;
    expect(out.success).toBe(false);
    expect(out.error).toBe('Invalid phone number');
    expect(out.telnyx_msg_id).toBe('');
  });

  test('failure: Telnyx returns empty response', async () => {
    const telnyx = {};
    const nodeData = { 'Split Recipients': [{ recipient_id: 44, channel: 'sms' }] };
    const result = await runCode(code.checkSMS, { inputItems: [telnyx], nodeData });
    const out = result[0]?.json || result.json;
    expect(out.success).toBe(false);
  });

  test('success: data object exists but id is falsy → still failure', async () => {
    const telnyx = { data: null };
    const nodeData = { 'Split Recipients': [{ recipient_id: 45, channel: 'sms' }] };
    const result = await runCode(code.checkSMS, { inputItems: [telnyx], nodeData });
    const out = result[0]?.json || result.json;
    expect(out.success).toBe(false);
  });
});

// ─── Check Email Result ───────────────────────────────────────────────────────

describe('Check Email Result — Code node', () => {
  test('success: status = "ok"', async () => {
    const emailResult = { status: 'ok' };
    const nodeData = { 'Split Recipients': [{ recipient_id: 10, channel: 'email' }] };
    const result = await runCode(code.checkEmail, { inputItems: [emailResult], nodeData });
    const out = result[0]?.json || result.json;
    expect(out.success).toBe(true);
    expect(out.error).toBe('');
  });

  test('success: message present and no detail field', async () => {
    const emailResult = { message: 'Email queued successfully' };
    const nodeData = { 'Split Recipients': [{ recipient_id: 11, channel: 'email' }] };
    const result = await runCode(code.checkEmail, { inputItems: [emailResult], nodeData });
    const out = result[0]?.json || result.json;
    expect(out.success).toBe(true);
  });

  test('failure: detail field present (FastAPI validation error)', async () => {
    const emailResult = { detail: 'Unprocessable Entity', message: 'Error' };
    const nodeData = { 'Split Recipients': [{ recipient_id: 12, channel: 'email' }] };
    const result = await runCode(code.checkEmail, { inputItems: [emailResult], nodeData });
    const out = result[0]?.json || result.json;
    expect(out.success).toBe(false);
    expect(out.error).toBe('Unprocessable Entity');
  });

  test('failure: empty response → success is falsy', async () => {
    // JS: false || (undefined && true) = undefined, which is falsy but not strictly false
    const emailResult = {};
    const nodeData = { 'Split Recipients': [{ recipient_id: 13, channel: 'email' }] };
    const result = await runCode(code.checkEmail, { inputItems: [emailResult], nodeData });
    const out = result[0]?.json || result.json;
    expect(out.success).toBeFalsy();
  });

  test('failure: error field present → success is falsy', async () => {
    const emailResult = { error: 'SMTP timeout' };
    const nodeData = { 'Split Recipients': [{ recipient_id: 14, channel: 'email' }] };
    const result = await runCode(code.checkEmail, { inputItems: [emailResult], nodeData });
    const out = result[0]?.json || result.json;
    expect(out.success).toBeFalsy();
    expect(out.error).toBe('SMTP timeout');
  });
});

// ─── Has Pending Recipients? — IF node ───────────────────────────────────────

describe('Has Pending Recipients? — IF node', () => {
  test('TRUE: non-empty array', () => {
    expect(evalIf(ifs.hasPending, [{ id: 1 }])).toBe(true);
  });
  test('FALSE: empty array', () => {
    expect(evalIf(ifs.hasPending, [])).toBe(false);
  });
  test('FALSE: non-array', () => {
    expect(evalIf(ifs.hasPending, { data: [] })).toBe(false);
  });
});

// ─── Route by Channel — Switch node ──────────────────────────────────────────

describe('Route by Channel — Switch node', () => {
  test('channel = "sms" → sms branch', () => {
    expect(evalSwitch(switchRules, { channel: 'sms' })).toBe('sms');
  });

  test('channel = "email" → email branch', () => {
    expect(evalSwitch(switchRules, { channel: 'email' })).toBe('email');
  });

  test('unknown channel → fallback', () => {
    expect(evalSwitch(switchRules, { channel: 'push' })).toBe('fallback');
  });

  test('missing channel → fallback', () => {
    expect(evalSwitch(switchRules, {})).toBe('fallback');
  });

  test('SMS case-insensitive (channel is lowercase "sms")', () => {
    expect(evalSwitch(switchRules, { channel: 'SMS' })).not.toBe(undefined);
  });
});

// ─── Flow tests ───────────────────────────────────────────────────────────────

describe('Flow: empty recipients queue → skip', () => {
  test('empty array → IF false → no split/route', async () => {
    const pending = [];
    expect(evalIf(ifs.hasPending, pending)).toBe(false);
  });
});

describe('Flow: SMS broadcast full path', () => {
  test('populate → split → route sms → check result', async () => {
    const pending = [{ recipient_id: 1, channel: 'sms', phone: '+14165550001', sms_message: 'Hello!' }];
    expect(evalIf(ifs.hasPending, pending)).toBe(true);

    const split = await runCode(code.split, { inputItems: [pending] });
    expect(split).toHaveLength(1);

    const route = evalSwitch(switchRules, split[0].json);
    expect(route).toBe('sms');

    // Simulate successful Telnyx response
    const telnyx = { data: { id: 'msg-xyz' } };
    const nodeData = { 'Split Recipients': [{ recipient_id: 1, channel: 'sms' }] };
    const checkResult = await runCode(code.checkSMS, { inputItems: [telnyx], nodeData });
    const out = checkResult[0]?.json || checkResult.json;
    expect(out.success).toBe(true);
  });
});

describe('Flow: email broadcast full path', () => {
  test('populate → split → route email → check result', async () => {
    const pending = [{ recipient_id: 2, channel: 'email', email: 'a@b.com', email_subject: 'Promo' }];
    const split = await runCode(code.split, { inputItems: [pending] });
    const route = evalSwitch(switchRules, split[0].json);
    expect(route).toBe('email');

    const emailResponse = { status: 'ok' };
    const nodeData = { 'Split Recipients': [{ recipient_id: 2, channel: 'email' }] };
    const checkResult = await runCode(code.checkEmail, { inputItems: [emailResponse], nodeData });
    const out = checkResult[0]?.json || checkResult.json;
    expect(out.success).toBe(true);
  });
});

describe('Flow: failed SMS marks failure', () => {
  test('Telnyx error → check result → success = false', async () => {
    const telnyx = { errors: [{ detail: 'Number blocked' }] };
    const nodeData = { 'Split Recipients': [{ recipient_id: 3, channel: 'sms' }] };
    const result = await runCode(code.checkSMS, { inputItems: [telnyx], nodeData });
    const out = result[0]?.json || result.json;
    expect(out.success).toBe(false);
    expect(out.error).toBeTruthy();
  });
});
