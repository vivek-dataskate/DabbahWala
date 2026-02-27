'use strict';
/**
 * Tests for [Chatbot] Query Form — marketing_query_form.json
 *
 * Covers:
 *  - Code: Map Category     — maps form dropdown to API category, detects is_submission
 *  - Code: Format Response  — produces formatted_answer with borders
 *  - IF:   Is Submission?   — TRUE (is_submission=true) / FALSE (is_submission=false)
 *  - HTTP: Call Query API   — POST to /api/query, timeout=90s
 */

const { loadWorkflow, getCodeNode, getIfConditions, getNode, runCode, evalIf } = require('../helpers/runtime');

let wf, mapCode, formatCode, ifc;
beforeAll(() => {
  wf         = loadWorkflow('marketing_query_form.json');
  mapCode    = getCodeNode(wf, 'Map Category');
  formatCode = getCodeNode(wf, 'Format Response');
  ifc        = getIfConditions(wf, 'Is Submission?');
});

// ─── Map Category — Code node ─────────────────────────────────────────────────

describe('Map Category — Code node', () => {
  const makeInput = (category, extra = {}) => ({
    inputItems: [{
      json: {
        'Query Category': category,
        'Your Question or Details': 'Test question',
        ...extra,
      },
    }],
  });

  test('Pipeline Snapshot → pipeline_snapshot', async () => {
    const [out] = await runCode(mapCode, makeInput('Pipeline Snapshot — Lifecycle stage distribution'));
    expect(out.json.category).toBe('pipeline_snapshot');
    expect(out.json.is_submission).toBe(false);
  });

  test('Who to Contact Today → who_to_contact', async () => {
    const [out] = await runCode(mapCode, makeInput('Who to Contact Today — Prioritized outreach list'));
    expect(out.json.category).toBe('who_to_contact');
  });

  test('Submit New Input → submit_input + is_submission=true', async () => {
    const [out] = await runCode(mapCode, makeInput('Submit New Input — Add your own observation or note'));
    expect(out.json.category).toBe('submit_input');
    expect(out.json.is_submission).toBe(true);
  });

  test('Ask Anything (AI) → free_form', async () => {
    const [out] = await runCode(mapCode, makeInput('Ask Anything (AI) — Free-form question answered by AI'));
    expect(out.json.category).toBe('free_form');
  });

  test('unknown category defaults to free_form', async () => {
    const [out] = await runCode(mapCode, makeInput('Something Else Entirely'));
    expect(out.json.category).toBe('free_form');
  });

  test('customer_lookup maps contact_email from form field', async () => {
    const [out] = await runCode(mapCode, makeInput(
      'Customer Lookup — Look up a specific customer',
      { 'Customer Email (if looking up a specific customer)': 'vivek@test.com' },
    ));
    expect(out.json.category).toBe('customer_lookup');
    expect(out.json.contact_email).toBe('vivek@test.com');
  });
});

// ─── Format Response — Code node ─────────────────────────────────────────────

describe('Format Response — Code node', () => {
  const makeCtx = (response, mapResult = {}) => ({
    inputItems: [{ json: response }],
    nodeData: { 'Map Category': [{ json: { is_submission: false, author: 'Test', input_type: 'observation', ...mapResult } }] },
  });

  test('produces formatted_answer with DabbahWala header', async () => {
    const [out] = await runCode(formatCode, makeCtx({ answer: 'Test answer', category: 'pipeline_snapshot' }));
    expect(out.json.formatted_answer).toContain('DabbahWala');
    expect(out.json.formatted_answer).toContain('Test answer');
  });

  test('includes category in uppercase in formatted output', async () => {
    const [out] = await runCode(formatCode, makeCtx({ answer: 'Answer.', category: 'pipeline_snapshot' }));
    expect(out.json.formatted_answer).toContain('PIPELINE SNAPSHOT');
  });

  test('sets raw_answer field', async () => {
    const [out] = await runCode(formatCode, makeCtx({ answer: 'Raw answer here.', category: 'free_form' }));
    expect(out.json.raw_answer).toBe('Raw answer here.');
  });

  test('includes timestamp', async () => {
    const [out] = await runCode(formatCode, makeCtx({ answer: 'A', category: 'free_form' }));
    expect(out.json.timestamp).toBeDefined();
    expect(new Date(out.json.timestamp).getFullYear()).toBeGreaterThan(2020);
  });

  test('handles missing answer gracefully', async () => {
    const [out] = await runCode(formatCode, makeCtx({ category: 'free_form' }));
    expect(out.json.formatted_answer).toContain('No answer received');
  });
});

// ─── Is Submission? — IF node ─────────────────────────────────────────────────

describe('Is Submission? — IF node', () => {
  test('TRUE: is_submission=true → store path', () => {
    expect(evalIf(ifc, { is_submission: true })).toBe(true);
  });
  test('FALSE: is_submission=false', () => {
    expect(evalIf(ifc, { is_submission: false })).toBe(false);
  });
});

// ─── HTTP node config ─────────────────────────────────────────────────────────

describe('Call Query API — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Call Query API').parameters.method).toBe('POST');
  });
  test('URL targets /api/query', () => {
    expect(getNode(wf, 'Call Query API').parameters.url).toContain('/api/query');
  });
  test('timeout is at least 90 seconds', () => {
    expect(getNode(wf, 'Call Query API').parameters.options?.timeout).toBeGreaterThanOrEqual(90000);
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('has a form trigger', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.formTrigger')).toBeDefined();
  });
  test('has Is Submission? IF node', () => {
    expect(getNode(wf, 'Is Submission?').type).toBe('n8n-nodes-base.if');
  });
});
