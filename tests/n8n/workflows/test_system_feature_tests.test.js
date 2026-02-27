'use strict';
/**
 * Tests for [System] Feature Tests — system_feature_tests.json
 *
 * Covers:
 *  - Code: Summarize Results — builds HTML email from per-group test results
 *  - HTTP: G1–G14 test runners — all GET /api/test/run/{n} with continueOnFail
 *  - HTTP: Send Test Report Email — POST to /api/internal/send-email
 *  - Workflow structure — daily 5AM schedule, 11 test group nodes
 */

const { loadWorkflow, getCodeNode, getNode, runCode } = require('../helpers/runtime');

let wf, summarizeCode;
beforeAll(() => {
  wf            = loadWorkflow('system_feature_tests.json');
  summarizeCode = getCodeNode(wf, 'Summarize Results');
});

// ─── Summarize Results — Code node ───────────────────────────────────────────

describe('Summarize Results — Code node', () => {
  const makeInputs = (groups) => ({
    inputItems: groups.map(g => ({ json: g })),
  });

  test('produces output with subject or html field', async () => {
    const groups = [
      { passed: 5, failed: 0, skipped: 0, group: 'G1 Connectivity' },
      { passed: 3, failed: 1, skipped: 0, group: 'G2 Schema' },
    ];
    const [out] = await runCode(summarizeCode, makeInputs(groups));
    const hasHtml  = out.json.body_html || out.json.html;
    const hasSubj  = out.json.subject;
    const hasTo    = out.json.to;
    expect(hasHtml || hasSubj || hasTo).toBeTruthy();
  });

  test('runs without error on empty input', async () => {
    await expect(runCode(summarizeCode, { inputItems: [] })).resolves.toBeDefined();
  });

  test('runs without error on single passing group', async () => {
    const [out] = await runCode(summarizeCode, makeInputs([{ passed: 10, failed: 0 }]));
    expect(out).toBeDefined();
  });
});

// ─── Test runner HTTP nodes ───────────────────────────────────────────────────

describe('Test runner HTTP nodes — all groups', () => {
  const groupNodes = [
    { name: 'G1 Connectivity', group: 1 },
    { name: 'G2 Schema',       group: 2 },
    { name: 'G3 Contact Setup', group: 3 },
    { name: 'G5 Telnyx SMS',   group: 5 },
    { name: 'G7 Intelligence', group: 7 },
    { name: 'G8 Instantly',    group: 8 },
    { name: 'G9 Airtable',     group: 9 },
    { name: 'G11 Orders',      group: 11 },
    { name: 'G12 Reports',     group: 12 },
    { name: 'G13 Chatbot',     group: 13 },
    { name: 'G14 Cleanup',     group: 14 },
  ];

  for (const { name, group } of groupNodes) {
    test(`${name} → GET /api/test/run/${group}`, () => {
      const node = getNode(wf, name);
      expect(node.parameters.url).toContain(`/api/test/run/${group}`);
    });
  }

  test('all test runner nodes have continueOnFail=true', () => {
    for (const { name } of groupNodes) {
      const node = getNode(wf, name);
      const continueOnFail =
        node.onError === 'continueRegularOutput' ||
        node.parameters.options?.continueOnFail === true ||
        node.continueOnFail === true;
      expect(continueOnFail).toBe(true);
    }
  });
});

// ─── Send Test Report Email ───────────────────────────────────────────────────

describe('Send Test Report Email — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Send Test Report Email').parameters.method).toBe('POST');
  });
  test('URL targets /api/internal/send-email', () => {
    expect(getNode(wf, 'Send Test Report Email').parameters.url).toContain('/send-email');
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('runs daily at 5 AM', () => {
    const trigger = wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger');
    expect(trigger).toBeDefined();
    const rule = trigger.parameters?.rule;
    const hours = rule?.interval?.[0]?.triggerAtHour ?? rule?.triggerAtHour;
    expect(hours).toBe(5);
  });

  test('has Summarize Results Code node', () => {
    expect(getNode(wf, 'Summarize Results').type).toBe('n8n-nodes-base.code');
  });
});
