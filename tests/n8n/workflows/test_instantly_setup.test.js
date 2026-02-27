'use strict';
/**
 * Tests for [Email Campaigns] Campaign Setup — instantly_setup.json
 *
 * Covers:
 *  - Code: Parse Response — handles already_setup and full-setup response
 *  - HTTP: POST /api/campaigns/setup-instantly — POST, timeout=60s
 *  - Workflow structure
 */

const { loadWorkflow, getCodeNode, getNode, runCode } = require('../helpers/runtime');

let wf, parseCode;
beforeAll(() => {
  wf        = loadWorkflow('instantly_setup.json');
  parseCode = getCodeNode(wf, 'Parse Response');
});

// ─── Parse Response — Code node ──────────────────────────────────────────────

describe('Parse Response — Code node', () => {
  test('already_setup → skipped=true with message and campaign_count', async () => {
    const body = { status: 'already_setup', message: 'Campaigns already configured.', campaign_count: 6 };
    const [out] = await runCode(parseCode, { inputItems: [{ json: body }] });
    expect(out.json.skipped).toBe(true);
    expect(out.json.message).toBe('Campaigns already configured.');
    expect(out.json.campaign_count).toBe(6);
  });

  test('full setup response → skipped=false with campaign data', async () => {
    const body = {
      status: 'setup_complete',
      created_campaigns: { NURTURE_SLOW: 'uuid-1' },
      resolved_campaign_ids: { NURTURE_SLOW: 'uuid-1', PROMO_STANDARD: 'uuid-2' },
      active_customer_campaign_id: 'uuid-3',
      configure_results: { total: 6 },
      dedup_results: { deduped: 2 },
    };
    const [out] = await runCode(parseCode, { inputItems: [{ json: body }] });
    expect(out.json.skipped).toBe(false);
    expect(out.json.campaign_count).toBe(2);
    expect(out.json.created_campaigns).toBeDefined();
    expect(out.json.active_customer_id).toBe('uuid-3');
  });

  test('campaign_count reflects number of resolved campaign IDs', async () => {
    const body = {
      status: 'setup_complete',
      resolved_campaign_ids: { A: '1', B: '2', C: '3' },
    };
    const [out] = await runCode(parseCode, { inputItems: [{ json: body }] });
    expect(out.json.campaign_count).toBe(3);
  });
});

// ─── HTTP node config ─────────────────────────────────────────────────────────

describe('POST /api/campaigns/setup-instantly — HTTP node', () => {
  test('method is POST', () => {
    const node = getNode(wf, 'POST /api/campaigns/setup-instantly');
    expect(node.parameters.method).toBe('POST');
  });
  test('URL targets /api/campaigns/setup-instantly', () => {
    const node = getNode(wf, 'POST /api/campaigns/setup-instantly');
    expect(node.parameters.url).toContain('/campaigns/setup-instantly');
  });
  test('timeout is at least 60 seconds', () => {
    const node = getNode(wf, 'POST /api/campaigns/setup-instantly');
    expect(node.parameters.options?.timeout).toBeGreaterThanOrEqual(60000);
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('has a daily schedule trigger', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeDefined();
  });
  test('has Done terminal node', () => {
    expect(getNode(wf, 'Done')).toBeDefined();
  });
});
