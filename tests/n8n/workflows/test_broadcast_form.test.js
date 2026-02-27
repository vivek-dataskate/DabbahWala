'use strict';
/**
 * Tests for broadcast_form.json
 *
 * Covers every Code node and every IF branch:
 *  Code: Parse Form Input  — channel options (sms+email, sms-only, email-only), delay vs promo
 *  IF:   Delay or Promo?   — TRUE (is_delay=true) → Format Delay Result
 *                            FALSE (is_delay=false) → Format Promo Result
 *  Code: Format Delay Result  — success (status=queued) and failure paths
 *  Code: Format Promo Result  — success (status=queued) and failure paths
 *
 *  Flow: Parse → IF TRUE  → Delay path (delay alert queued / error)
 *        Parse → IF FALSE → Promo path (promo queued / error)
 *
 *  Cleanup: pure JS unit tests — no external state to clean up.
 */

const { runCode, evalIf, loadWorkflow, getCodeNode, getIfConditions } = require('../helpers/runtime');

let wf;
const code = {};
let ifDelayOrPromo;

beforeAll(() => {
  wf = loadWorkflow('broadcast_form.json');
  code.parseInput   = getCodeNode(wf, 'Parse Form Input');
  code.formatDelay  = getCodeNode(wf, 'Format Delay Result');
  code.formatPromo  = getCodeNode(wf, 'Format Promo Result');
  ifDelayOrPromo    = getIfConditions(wf, 'Delay or Promo?');
});

afterAll(() => {
  // No external state — nothing to clean up
  wf = null;
});

// ─── Parse Form Input — Code node ────────────────────────────────────────────

describe('Parse Form Input — Code node', () => {
  const makeForm = (overrides = {}) => ({
    'Broadcast Type':    'Promo Blast',
    'Promo Channels':    'SMS and Email',
    'SMS Message':       'Hello from DabbahWala!',
    'Email Subject':     'Special offer',
    'Email Body (HTML)': '<p>Big promo</p>',
    'Promo Title (for Promotional Blast only)': 'Weekly Promo',
    'Your Name':         'Ali',
    ...overrides,
  });

  test('Promo Blast → is_delay=false, channels=[sms,email]', async () => {
    const result = await runCode(code.parseInput, { inputItems: [makeForm()] });
    const out = result[0].json;
    expect(out.is_delay).toBe(false);
    expect(out.channels).toEqual(['sms', 'email']);
    expect(out.sms_message).toBe('Hello from DabbahWala!');
    expect(out.email_subject).toBe('Special offer');
    expect(out.promo_title).toBe('Weekly Promo');
    expect(out.created_by).toBe('Ali');
  });

  test('Delay Alert type → is_delay=true', async () => {
    const result = await runCode(code.parseInput, { inputItems: [makeForm({ 'Broadcast Type': 'Delay Alert — Orders delayed today' })] });
    expect(result[0].json.is_delay).toBe(true);
  });

  test('"Delay Alert" prefix (any variant) → is_delay=true', async () => {
    const result = await runCode(code.parseInput, { inputItems: [makeForm({ 'Broadcast Type': 'Delay Alert' })] });
    expect(result[0].json.is_delay).toBe(true);
  });

  // Channel option: SMS only
  test('Promo Channels = "SMS only" → channels=["sms"]', async () => {
    const result = await runCode(code.parseInput, { inputItems: [makeForm({ 'Promo Channels': 'SMS only' })] });
    expect(result[0].json.channels).toEqual(['sms']);
  });

  // Channel option: Email only
  test('Promo Channels = "Email only" → channels=["email"]', async () => {
    const result = await runCode(code.parseInput, { inputItems: [makeForm({ 'Promo Channels': 'Email only' })] });
    expect(result[0].json.channels).toEqual(['email']);
  });

  // Channel option: SMS and Email (default)
  test('Promo Channels = "SMS and Email" → channels=["sms","email"]', async () => {
    const result = await runCode(code.parseInput, { inputItems: [makeForm({ 'Promo Channels': 'SMS and Email' })] });
    expect(result[0].json.channels).toEqual(['sms', 'email']);
  });

  // Channel option: missing → defaults to both
  test('Promo Channels missing → defaults to ["sms","email"]', async () => {
    const form = makeForm();
    delete form['Promo Channels'];
    const result = await runCode(code.parseInput, { inputItems: [form] });
    expect(result[0].json.channels).toEqual(['sms', 'email']);
  });

  test('target_date defaults to null when blank', async () => {
    const result = await runCode(code.parseInput, { inputItems: [makeForm()] });
    expect(result[0].json.target_date).toBeNull();
  });

  test('target_date is preserved when provided', async () => {
    const form = makeForm({ 'Affected Date (for Delay Alert, leave blank for today)': '2024-06-15' });
    const result = await runCode(code.parseInput, { inputItems: [form] });
    expect(result[0].json.target_date).toBe('2024-06-15');
  });

  test('promo_title defaults to "Promotional Blast" when missing', async () => {
    const form = makeForm();
    delete form['Promo Title (for Promotional Blast only)'];
    const result = await runCode(code.parseInput, { inputItems: [form] });
    expect(result[0].json.promo_title).toBe('Promotional Blast');
  });

  test('created_by defaults to null when Your Name is missing', async () => {
    const form = makeForm();
    delete form['Your Name'];
    const result = await runCode(code.parseInput, { inputItems: [form] });
    expect(result[0].json.created_by).toBeNull();
  });

  test('completely empty form returns safe defaults', async () => {
    const result = await runCode(code.parseInput, { inputItems: [{}] });
    const out = result[0].json;
    expect(out.is_delay).toBe(false);
    expect(out.channels).toEqual(['sms', 'email']);
    expect(out.sms_message).toBe('');
    expect(out.email_subject).toBe('');
  });
});

// ─── Delay or Promo? — IF node (both branches) ───────────────────────────────

describe('Delay or Promo? — IF node', () => {
  // TRUE branch: is_delay = true → goes to Format Delay Result
  test('TRUE branch: is_delay=true → delay path', () => {
    expect(evalIf(ifDelayOrPromo, { is_delay: true })).toBe(true);
  });

  // FALSE branch: is_delay = false → goes to Format Promo Result
  test('FALSE branch: is_delay=false → promo path', () => {
    expect(evalIf(ifDelayOrPromo, { is_delay: false })).toBe(false);
  });

  // FALSE branch: is_delay missing → defaults to falsy → promo path
  test('FALSE branch: is_delay missing → promo path', () => {
    expect(evalIf(ifDelayOrPromo, {})).toBe(false);
  });

  // TRUE branch: string 'true' (shouldn't happen but defensive)
  test('TRUE branch: boolean strict match — string "true" does NOT pass', () => {
    // n8n uses strict boolean comparison, so string 'true' should not match
    expect(evalIf(ifDelayOrPromo, { is_delay: 'true' })).toBe(false);
  });
});

// ─── Format Delay Result — Code node ─────────────────────────────────────────

describe('Format Delay Result — Code node', () => {
  // TRUE branch of Delay or Promo? → queued delay alert
  test('status=queued → success message with job_id, date, recipients', async () => {
    const resp = { status: 'queued', job_id: 'job-delay-001', target_date: '2024-06-15', total_recipients: 250 };
    const result = await runCode(code.formatDelay, { inputItems: [resp] });
    const out = result[0].json;
    expect(out.result_message).toContain('✅ Delay alert queued!');
    expect(out.result_message).toContain('job-delay-001');
    expect(out.result_message).toContain('2024-06-15');
    expect(out.result_message).toContain('250');
  });

  // Error path: status != queued
  test('status=error → error message with response body', async () => {
    const resp = { status: 'error', detail: 'Database connection failed' };
    const result = await runCode(code.formatDelay, { inputItems: [resp] });
    const out = result[0].json;
    expect(out.result_message).toContain('❌ Error:');
    expect(out.result_message).toContain('error');
  });

  test('status=failed → error message', async () => {
    const resp = { status: 'failed', message: 'No recipients found' };
    const result = await runCode(code.formatDelay, { inputItems: [resp] });
    expect(result[0].json.result_message).toContain('❌');
  });

  test('empty response → error message', async () => {
    const result = await runCode(code.formatDelay, { inputItems: [{}] });
    expect(result[0].json.result_message).toContain('❌');
  });
});

// ─── Format Promo Result — Code node ─────────────────────────────────────────

describe('Format Promo Result — Code node', () => {
  const makeContext = (queueStatus = 'queued', jobId = 'job-promo-001', totalRecipients = 500) => ({
    inputItems: [{ status: queueStatus, total_recipients: totalRecipients }],
    nodeData: {
      'Create Promo Job': [{ job_id: jobId }],
    },
  });

  // TRUE result: promo queued successfully
  test('status=queued → success message with job_id and recipients', async () => {
    const result = await runCode(code.formatPromo, makeContext('queued', 'job-promo-001', 500));
    const out = result[0].json;
    expect(out.result_message).toContain('✅ Promotional blast queued!');
    expect(out.result_message).toContain('job-promo-001');
    expect(out.result_message).toContain('500');
  });

  // Error result: queue API returned non-queued status
  test('status=error → error message with response body', async () => {
    const result = await runCode(code.formatPromo, makeContext('error'));
    const out = result[0].json;
    expect(out.result_message).toContain('❌ Error:');
  });

  test('status=failed → error message', async () => {
    const result = await runCode(code.formatPromo, makeContext('failed'));
    expect(result[0].json.result_message).toContain('❌');
  });

  test('zero recipients → includes 0 in message', async () => {
    const result = await runCode(code.formatPromo, makeContext('queued', 'job-001', 0));
    expect(result[0].json.result_message).toContain('0');
  });
});

// ─── Flow: Delay Alert full path ──────────────────────────────────────────────

describe('Flow: Delay Alert → IF TRUE → Format Delay Result', () => {
  test('form with "Delay Alert" type → parse → IF routes to delay branch → format success', async () => {
    // Step 1: parse form
    const form = {
      'Broadcast Type': 'Delay Alert — Kitchen issue',
      'SMS Message': 'Deliveries delayed today, sorry!',
      'Affected Date (for Delay Alert, leave blank for today)': '2024-06-15',
    };
    const parseResult = await runCode(code.parseInput, { inputItems: [form] });
    const parsed = parseResult[0].json;
    expect(parsed.is_delay).toBe(true);

    // Step 2: IF routes to delay branch
    expect(evalIf(ifDelayOrPromo, parsed)).toBe(true);

    // Step 3: Format Delay Result
    const apiResp = { status: 'queued', job_id: 'delay-job-99', target_date: parsed.target_date, total_recipients: 320 };
    const formatResult = await runCode(code.formatDelay, { inputItems: [apiResp] });
    expect(formatResult[0].json.result_message).toContain('✅');
  });

  test('SMS+Email channels → both included in parse output', async () => {
    const form = {
      'Broadcast Type': 'Delay Alert — Late delivery',
      'Promo Channels': 'SMS and Email',
      'SMS Message': 'Delayed!',
    };
    const result = await runCode(code.parseInput, { inputItems: [form] });
    expect(result[0].json.channels).toEqual(['sms', 'email']);
    expect(result[0].json.is_delay).toBe(true);
  });
});

describe('Flow: Promo Blast → IF FALSE → Format Promo Result', () => {
  test('form with "Promo Blast" type → parse → IF routes to promo branch → format success', async () => {
    // Step 1: parse form
    const form = {
      'Broadcast Type': 'Promo Blast',
      'Promo Channels': 'SMS only',
      'SMS Message': 'Weekend special: 20% off!',
      'Promo Title (for Promotional Blast only)': 'Weekend Sale',
    };
    const parseResult = await runCode(code.parseInput, { inputItems: [form] });
    const parsed = parseResult[0].json;
    expect(parsed.is_delay).toBe(false);
    expect(parsed.channels).toEqual(['sms']);

    // Step 2: IF routes to promo branch
    expect(evalIf(ifDelayOrPromo, parsed)).toBe(false);

    // Step 3: Format Promo Result
    const queueResp = { status: 'queued', total_recipients: 180 };
    const nodeData = { 'Create Promo Job': [{ job_id: 'promo-job-55' }] };
    const formatResult = await runCode(code.formatPromo, { inputItems: [queueResp], nodeData });
    expect(formatResult[0].json.result_message).toContain('✅');
    expect(formatResult[0].json.result_message).toContain('promo-job-55');
  });

  test('Email-only promo → channels=["email"]', async () => {
    const form = {
      'Broadcast Type': 'Promo Blast',
      'Promo Channels': 'Email only',
      'Email Subject': 'Exclusive deal!',
      'Email Body (HTML)': '<p>Check this out</p>',
    };
    const result = await runCode(code.parseInput, { inputItems: [form] });
    const parsed = result[0].json;
    expect(parsed.channels).toEqual(['email']);
    expect(evalIf(ifDelayOrPromo, parsed)).toBe(false);
  });
});

describe('Flow: API error in both paths', () => {
  test('Delay path → API error → format shows ❌', async () => {
    const apiError = { status: 'error', detail: 'timeout' };
    const result = await runCode(code.formatDelay, { inputItems: [apiError] });
    expect(result[0].json.result_message).toContain('❌');
  });

  test('Promo path → queue error → format shows ❌', async () => {
    const queueError = { status: 'error', detail: 'db connection failed' };
    const nodeData = { 'Create Promo Job': [{ job_id: 'job-x' }] };
    const result = await runCode(code.formatPromo, { inputItems: [queueError], nodeData });
    expect(result[0].json.result_message).toContain('❌');
  });
});
