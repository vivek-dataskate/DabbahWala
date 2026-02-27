'use strict';
/**
 * Tests for [Growth] Competitor Research — competitor_agent_cycle.json
 *
 * Covers:
 *  - Code: Log Result — captures key output fields, adds timestamp
 *  - HTTP: Run Competitor Research Cycle — POST, correct URL, 5-min timeout
 *  - Workflow structure
 */

const { loadWorkflow, getCodeNode, getNode, runCode } = require('../helpers/runtime');

let wf, logCode;
beforeAll(() => {
  wf      = loadWorkflow('competitor_agent_cycle.json');
  logCode = getCodeNode(wf, 'Log Result');
});

// ─── Log Result — Code node ───────────────────────────────────────────────────

describe('Log Result — Code node', () => {
  test('passes through result fields from upstream HTTP node', async () => {
    const result = {
      timestamp: '2026-01-01T09:00:00Z',
      email_samples_parsed: 12,
      websites_scraped: 3,
      hypotheses_queued: 5,
    };
    const [out] = await runCode(logCode, {
      nodeData: { 'Run Competitor Research Cycle': [{ json: result }] },
    });
    expect(out.json.email_samples_parsed).toBe(12);
    expect(out.json.websites_scraped).toBe(3);
    expect(out.json.hypotheses_queued).toBe(5);
    expect(out.json.timestamp).toBe('2026-01-01T09:00:00Z');
  });

  test('includes summary string with parsed/scraped counts', async () => {
    const result = {
      timestamp: '2026-01-01T09:00:00Z',
      email_samples_parsed: 5,
      websites_scraped: 2,
      hypotheses_queued: 3,
    };
    const [out] = await runCode(logCode, {
      nodeData: { 'Run Competitor Research Cycle': [{ json: result }] },
    });
    expect(out.json.summary).toContain('5 emails');
    expect(out.json.summary).toContain('2 sites');
    expect(out.json.summary).toContain('3');
  });

  test('handles zero values gracefully', async () => {
    const result = { timestamp: null, email_samples_parsed: 0, websites_scraped: 0, hypotheses_queued: 0 };
    const [out] = await runCode(logCode, {
      nodeData: { 'Run Competitor Research Cycle': [{ json: result }] },
    });
    expect(out.json.email_samples_parsed).toBe(0);
  });
});

// ─── HTTP node config ─────────────────────────────────────────────────────────

describe('Run Competitor Research Cycle — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Run Competitor Research Cycle').parameters.method).toBe('POST');
  });
  test('URL targets /api/competitor-agent/run', () => {
    expect(getNode(wf, 'Run Competitor Research Cycle').parameters.url).toContain('/competitor-agent/run');
  });
  test('timeout is at least 5 minutes (300000 ms)', () => {
    expect(getNode(wf, 'Run Competitor Research Cycle').parameters.options?.timeout).toBeGreaterThanOrEqual(300000);
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('has a schedule trigger', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeDefined();
  });
  test('has Log Result Code node', () => {
    expect(getNode(wf, 'Log Result').type).toBe('n8n-nodes-base.code');
  });
});
