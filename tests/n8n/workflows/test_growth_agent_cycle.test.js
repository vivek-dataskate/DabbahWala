'use strict';
/**
 * Tests for [Growth] Weekly Growth Agent — growth_agent_cycle.json
 *
 * Covers:
 *  - Code: Build Weekly Report — produces HTML string with key sections
 *  - HTTP: Refresh Baseline — POST to /api/growth/baseline/update
 *  - HTTP: Measure Previous Experiments — POST to /api/growth/measure
 *  - HTTP: Design + Launch New Experiment — POST to /api/growth/run-cycle
 *  - HTTP: Email Growth Report — POST to /api/internal/send-email
 */

const { loadWorkflow, getCodeNode, getNode, runCode } = require('../helpers/runtime');

let wf, buildCode;
beforeAll(() => {
  wf        = loadWorkflow('growth_agent_cycle.json');
  buildCode = getCodeNode(wf, 'Build Weekly Report');
});

// ─── Build Weekly Report — Code node ─────────────────────────────────────────

describe('Build Weekly Report — Code node', () => {
  const makeCtx = (newExp, measured, insights) => ({
    nodeData: {
      'Design + Launch New Experiment': [{ json: newExp }],
      'Measure Previous Experiments':   [{ json: measured }],
      'Get Growth Insights':            [{ json: insights }],
    },
  });

  test('generates an HTML string', async () => {
    const ctx = makeCtx(
      { experiment_name: 'Test Exp', hypothesis: 'Test hypothesis' },
      { experiments_measured: 2, experiments_concluded: 1 },
      { insights: ['insight 1'] },
    );
    const [out] = await runCode(buildCode, ctx);
    const html = out.json.body_html || out.json.html || out.json.report || '';
    expect(typeof html).toBe('string');
    expect(html.length).toBeGreaterThan(50);
  });

  test('output contains subject or html field', async () => {
    const ctx = makeCtx(
      { experiment_name: 'Exp X' },
      { experiments_measured: 0 },
      { insights: [] },
    );
    const [out] = await runCode(buildCode, ctx);
    const hasHtml = out.json.body_html || out.json.html || out.json.report;
    const hasSubject = out.json.subject;
    expect(hasHtml || hasSubject).toBeTruthy();
  });
});

// ─── HTTP node configs ────────────────────────────────────────────────────────

describe('Refresh Baseline Conv Rate — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Refresh Baseline Conv Rate').parameters.method).toBe('POST');
  });
  test('URL targets /api/growth/baseline/update', () => {
    expect(getNode(wf, 'Refresh Baseline Conv Rate').parameters.url).toContain('/growth/baseline/update');
  });
});

describe('Measure Previous Experiments — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Measure Previous Experiments').parameters.method).toBe('POST');
  });
  test('URL targets /api/growth/measure', () => {
    expect(getNode(wf, 'Measure Previous Experiments').parameters.url).toContain('/growth/measure');
  });
});

describe('Design + Launch New Experiment — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Design + Launch New Experiment').parameters.method).toBe('POST');
  });
  test('URL targets /api/growth/run-cycle', () => {
    expect(getNode(wf, 'Design + Launch New Experiment').parameters.url).toContain('/growth/run-cycle');
  });
});

describe('Email Growth Report — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Email Growth Report').parameters.method).toBe('POST');
  });
  test('URL targets internal send-email', () => {
    expect(getNode(wf, 'Email Growth Report').parameters.url).toContain('/send-email');
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('has a weekly schedule trigger', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeDefined();
  });
  test('has Build Weekly Report Code node', () => {
    expect(getNode(wf, 'Build Weekly Report').type).toBe('n8n-nodes-base.code');
  });
});
