'use strict';
/**
 * Tests for [Email Campaigns] Performance Tracker — campaign_performance_tracker.json
 *
 * Covers:
 *  - Code: Split Into One Per Campaign — extracts campaign array, one item each
 *  - Code: Normalise Stats — maps Instantly analytics → Render shape, handles missing data
 *  - HTTP: Fetch Campaign Analytics — GET with Bearer auth, Instantly URL
 *  - HTTP: Save Stats to Render DB  — POST to /api/webhooks/campaign-stats
 */

const { loadWorkflow, getCodeNode, getNode, runCode } = require('../helpers/runtime');

let wf, splitCode, normaliseCode;
beforeAll(() => {
  wf           = loadWorkflow('campaign_performance_tracker.json');
  splitCode    = getCodeNode(wf, 'Split Into One Per Campaign');
  normaliseCode = getCodeNode(wf, 'Normalise Stats');
});

// ─── Split Into One Per Campaign — Code node ─────────────────────────────────

describe('Split Into One Per Campaign — Code node', () => {
  test('returns one item per campaign', async () => {
    const data = {
      campaigns: [
        { campaign_id: 'c1', campaign_name: 'Nurture' },
        { campaign_id: 'c2', campaign_name: 'Promo' },
      ],
    };
    const items = await runCode(splitCode, { inputItems: [{ json: data }] });
    expect(items).toHaveLength(2);
    expect(items[0].json.campaign_id).toBe('c1');
    expect(items[1].json.campaign_id).toBe('c2');
  });

  test('filters out campaigns without campaign_id', async () => {
    const data = {
      campaigns: [
        { campaign_id: 'c1' },
        { campaign_name: 'No ID' },
      ],
    };
    const items = await runCode(splitCode, { inputItems: [{ json: data }] });
    expect(items).toHaveLength(1);
    expect(items[0].json.campaign_id).toBe('c1');
  });

  test('returns empty array when no campaigns', async () => {
    const items = await runCode(splitCode, { inputItems: [{ json: { campaigns: [] } }] });
    expect(items).toHaveLength(0);
  });
});

// ─── Normalise Stats — Code node ─────────────────────────────────────────────

describe('Normalise Stats — Code node', () => {
  const nodeData = {
    'Split Into One Per Campaign': [{ json: { campaign_id: 'cX' } }],
  };

  test('maps Instantly analytics fields to normalised shape', async () => {
    const raw = {
      campaign_id: 'cX',
      emails_sent_count: 100,
      open_count: 40,
      open_count_unique: 35,
      reply_count: 10,
      link_click_count: 5,
      bounced_count: 2,
      unsubscribed_count: 1,
      leads_count: 80,
    };
    const [out] = await runCode(normaliseCode, { inputItems: [{ json: raw }], nodeData });
    expect(out.json.emails_sent).toBe(100);
    expect(out.json.opens).toBe(40);
    expect(out.json.unique_opens).toBe(35);
    expect(out.json.replies).toBe(10);
    expect(out.json.clicks).toBe(5);
    expect(out.json.bounces).toBe(2);
    expect(out.json.unsubscribes).toBe(1);
    expect(out.json.leads_count).toBe(80);
  });

  test('calculates open_rate and reply_rate', async () => {
    const raw = { emails_sent_count: 100, open_count: 25, reply_count: 10 };
    const [out] = await runCode(normaliseCode, { inputItems: [{ json: raw }], nodeData });
    expect(out.json.open_rate).toBe(25);
    expect(out.json.reply_rate).toBe(10);
  });

  test('returns zero stats when emails_sent_count missing (campaign not launched)', async () => {
    const raw = { campaign_id: 'cX' };
    const [out] = await runCode(normaliseCode, { inputItems: [{ json: raw }], nodeData });
    expect(out.json.emails_sent).toBe(0);
    expect(out.json.opens).toBe(0);
    expect(out.json.open_rate).toBe(0);
    expect(out.json.reply_rate).toBe(0);
  });

  test('handles array response from Instantly (unwraps first element)', async () => {
    const raw = [{ campaign_id: 'cX', emails_sent_count: 50, open_count: 10, reply_count: 5 }];
    const [out] = await runCode(normaliseCode, { inputItems: [{ json: raw }], nodeData });
    expect(out.json.emails_sent).toBe(50);
  });

  test('open_rate is 0 when no emails sent', async () => {
    const raw = { campaign_id: 'cX', emails_sent_count: 0, open_count: 0, reply_count: 0 };
    const [out] = await runCode(normaliseCode, { inputItems: [{ json: raw }], nodeData });
    expect(out.json.open_rate).toBe(0);
    expect(out.json.reply_rate).toBe(0);
  });
});

// ─── HTTP node configs ────────────────────────────────────────────────────────

describe('Fetch Campaign Analytics — HTTP node', () => {
  test('URL targets Instantly analytics endpoint', () => {
    const node = getNode(wf, 'Fetch Campaign Analytics from Instantly');
    expect(node.parameters.url).toContain('instantly.ai');
    expect(node.parameters.url).toContain('analytics');
  });
});

describe('Save Stats to Render DB — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Save Stats to Render DB').parameters.method).toBe('POST');
  });
  test('URL targets /api/webhooks/campaign-stats', () => {
    expect(getNode(wf, 'Save Stats to Render DB').parameters.url).toContain('/campaign-stats');
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('has a schedule trigger', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeDefined();
  });
  test('has Done terminal node', () => {
    expect(getNode(wf, 'Done')).toBeDefined();
  });
});
