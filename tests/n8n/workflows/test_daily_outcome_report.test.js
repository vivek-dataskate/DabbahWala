'use strict';
/**
 * Tests for daily_outcome_report.json
 *
 * Workflow: [Reports] Daily Outcome Report
 * Schedule: Daily at 8:30 AM (30 8 * * *)
 *
 * Nodes under test:
 *   Code: "Get Today's Date"  — returns a single item with today's ISO date string
 *   IF:   "Email Sent?"       — $json.status === 'sent'
 *                               TRUE  → Done (noOp)
 *                               FALSE → Failed — Check Logs (noOp)
 *
 * Flow:
 *   Trigger → Get Today's Date → Run Outcome Report Agent (HTTP) → Email Sent? → Done / Failed
 *
 * Cleanup: pure JS unit tests — no external state to clean up.
 */

const { runCode, evalIf, loadWorkflow, getCodeNode, getIfConditions } = require('../helpers/runtime');

let wf;
let codeGetDate;
let ifEmailSent;

beforeAll(() => {
  wf = loadWorkflow('daily_outcome_report.json');
  codeGetDate = getCodeNode(wf, "Get Today's Date");
  ifEmailSent = getIfConditions(wf, 'Email Sent?');
});

afterAll(() => {
  wf = null;
});

// ─── Workflow metadata ────────────────────────────────────────────────────────

describe('Workflow metadata', () => {
  test('workflow name is correct', () => {
    expect(wf.name).toBe('[Reports] Daily Outcome Report');
  });

  test('schedule trigger fires at 8:30 AM daily', () => {
    const trigger = wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger');
    expect(trigger).toBeDefined();
    const expr = trigger.parameters.rule.interval[0].expression;
    expect(expr).toBe('30 8 * * *');
  });

  test('schedule is 30 minutes after the activity report (8 AM vs 8:30 AM)', () => {
    const trigger = wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger');
    const expr = trigger.parameters.rule.interval[0].expression;
    // cron: minute=30, hour=8
    const [minute, hour] = expr.split(' ');
    expect(minute).toBe('30');
    expect(hour).toBe('8');
  });

  test('HTTP request targets the outcome report endpoint', () => {
    const http = wf.nodes.find(n => n.name === 'Run Outcome Report Agent');
    expect(http).toBeDefined();
    expect(http.parameters.url).toBe(
      'https://dabbahwala-latest.onrender.com/api/agents/report/outcome'
    );
    expect(http.parameters.method).toBe('POST');
  });

  test('outcome endpoint differs from activity endpoint', () => {
    const http = wf.nodes.find(n => n.name === 'Run Outcome Report Agent');
    expect(http.parameters.url).not.toContain('activity');
    expect(http.parameters.url).toContain('outcome');
  });

  test('HTTP request has 120 second timeout', () => {
    const http = wf.nodes.find(n => n.name === 'Run Outcome Report Agent');
    expect(http.parameters.options.timeout).toBe(120000);
  });
});

// ─── Code: Get Today's Date ───────────────────────────────────────────────────

describe("Get Today's Date — Code node", () => {
  test('returns exactly one item', async () => {
    const result = await runCode(codeGetDate, {});
    expect(result).toHaveLength(1);
  });

  test('output has a report_date field', async () => {
    const result = await runCode(codeGetDate, {});
    expect(result[0].json).toHaveProperty('report_date');
  });

  test('report_date matches YYYY-MM-DD format', async () => {
    const result = await runCode(codeGetDate, {});
    const date = result[0].json.report_date;
    expect(date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  test('report_date is todays date', async () => {
    const result = await runCode(codeGetDate, {});
    const date = result[0].json.report_date;
    const expected = new Date().toISOString().split('T')[0];
    expect(date).toBe(expected);
  });

  test('report_date contains no time component', async () => {
    const result = await runCode(codeGetDate, {});
    const date = result[0].json.report_date;
    expect(date).not.toContain('T');
    expect(date).not.toContain(':');
    expect(date).not.toContain('Z');
  });

  test('report_date is a valid calendar date', async () => {
    const result = await runCode(codeGetDate, {});
    const date = result[0].json.report_date;
    const parsed = new Date(date);
    expect(parsed.toString()).not.toBe('Invalid Date');
  });

  test('output json has only the report_date key', async () => {
    const result = await runCode(codeGetDate, {});
    const keys = Object.keys(result[0].json);
    expect(keys).toEqual(['report_date']);
  });

  test('date code is identical to daily_activity_report Get Today\'s Date', async () => {
    // Both reports use the exact same date extraction logic
    const activityWf = require('../../../n8n/daily_activity_report.json');
    const activityDateCode = activityWf.nodes.find(n => n.name === "Get Today's Date").parameters.jsCode;
    expect(codeGetDate).toBe(activityDateCode);
  });
});

// ─── IF: Email Sent? ──────────────────────────────────────────────────────────

describe('Email Sent? — IF node', () => {
  // TRUE branch → Done
  test('TRUE branch: status="sent" → email was sent', () => {
    expect(evalIf(ifEmailSent, { status: 'sent' })).toBe(true);
  });

  // FALSE branch → Failed — Check Logs
  test('FALSE branch: status="error" → not sent', () => {
    expect(evalIf(ifEmailSent, { status: 'error' })).toBe(false);
  });

  test('FALSE branch: status="skipped" → not sent', () => {
    expect(evalIf(ifEmailSent, { status: 'skipped' })).toBe(false);
  });

  test('FALSE branch: status="failed" → not sent', () => {
    expect(evalIf(ifEmailSent, { status: 'failed' })).toBe(false);
  });

  test('FALSE branch: status missing → not sent', () => {
    expect(evalIf(ifEmailSent, {})).toBe(false);
  });

  test('FALSE branch: status=null → not sent', () => {
    expect(evalIf(ifEmailSent, { status: null })).toBe(false);
  });

  test('FALSE branch: status="" (empty string) → not sent', () => {
    expect(evalIf(ifEmailSent, { status: '' })).toBe(false);
  });

  test('FALSE branch: status="SENT" (uppercase) does not match', () => {
    expect(evalIf(ifEmailSent, { status: 'SENT' })).toBe(false);
  });

  test('FALSE branch: status="Sent" (mixed case) does not match', () => {
    expect(evalIf(ifEmailSent, { status: 'Sent' })).toBe(false);
  });

  test('FALSE branch: status is a number → does not match string "sent"', () => {
    expect(evalIf(ifEmailSent, { status: 1 })).toBe(false);
  });

  test('FALSE branch: status=true (boolean) → does not match', () => {
    expect(evalIf(ifEmailSent, { status: true })).toBe(false);
  });
});

// ─── IF node structure validation ────────────────────────────────────────────

describe('Email Sent? — IF node structure', () => {
  test('IF node uses string equals operator', () => {
    const cond = ifEmailSent.conditions[0];
    expect(cond.operator.type).toBe('string');
    expect(cond.operator.operation).toBe('equals');
  });

  test('IF node right value is "sent"', () => {
    const cond = ifEmailSent.conditions[0];
    expect(cond.rightValue).toBe('sent');
  });

  test('IF node left value references $json.status', () => {
    const cond = ifEmailSent.conditions[0];
    expect(cond.leftValue).toContain('$json.status');
  });

  test('IF node combinator is "and"', () => {
    expect(ifEmailSent.combinator).toBe('and');
  });
});

// ─── Flow integration ─────────────────────────────────────────────────────────

describe('Flow: Get Today\'s Date → Email Sent? routing', () => {
  test('date node output flows into email check — status=sent routes to Done', async () => {
    const dateResult = await runCode(codeGetDate, {});
    expect(dateResult[0].json.report_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);

    const apiResponse = { report_date: dateResult[0].json.report_date, status: 'sent' };
    expect(evalIf(ifEmailSent, apiResponse)).toBe(true);
  });

  test('date node output flows into email check — status=error routes to Failed', async () => {
    const dateResult = await runCode(codeGetDate, {});
    const apiResponse = { report_date: dateResult[0].json.report_date, status: 'error' };
    expect(evalIf(ifEmailSent, apiResponse)).toBe(false);
  });

  test('API response with no_data status routes to Failed — Check Logs', async () => {
    const apiResponse = { status: 'no_data', reason: 'no orders today' };
    expect(evalIf(ifEmailSent, apiResponse)).toBe(false);
  });

  test('API response with extra fields — only status determines routing', async () => {
    const apiResponse = {
      status: 'sent',
      email_to: 'core@dabbahwala.com',
      orders_today: 47,
      revenue: 1234.56,
    };
    expect(evalIf(ifEmailSent, apiResponse)).toBe(true);
  });
});
