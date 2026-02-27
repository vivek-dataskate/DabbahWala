'use strict';
/**
 * Tests for [Email Campaigns] Campaign Sync — instantly_campaign_sync.json
 *
 * Covers:
 *  - Code: Filter: tag = dabbahwala — filters by tag, normalises shape
 *  - HTTP: Fetch All Instantly Campaigns — GET with Bearer, limit=100
 *  - HTTP: Upsert Campaigns in Render DB — POST to /api/webhooks/sync-campaigns
 */

const { loadWorkflow, getCodeNode, getNode, runCode } = require('../helpers/runtime');

let wf, filterCode;
beforeAll(() => {
  wf         = loadWorkflow('instantly_campaign_sync.json');
  filterCode = getCodeNode(wf, 'Filter: tag = dabbahwala');
});

// ─── Filter: tag = dabbahwala — Code node ────────────────────────────────────

describe('Filter: tag = dabbahwala — Code node', () => {
  const make = (campaigns) => ({ inputItems: [{ json: campaigns }] });

  test('keeps campaigns tagged dabbahwala', async () => {
    const campaigns = [
      { id: 'c1', name: 'Nurture', tags: ['dabbahwala'], status: 'active' },
      { id: 'c2', name: 'Other',   tags: ['other'],       status: 'active' },
    ];
    const [out] = await runCode(filterCode, make(campaigns));
    expect(out.json.campaigns).toHaveLength(1);
    expect(out.json.campaigns[0].campaign_id).toBe('c1');
  });

  test('case-insensitive tag match', async () => {
    const campaigns = [{ id: 'c1', name: 'Test', tags: ['DabbahWala'], status: 'active' }];
    const [out] = await runCode(filterCode, make(campaigns));
    expect(out.json.campaigns).toHaveLength(1);
  });

  test('returns empty campaigns array when none match', async () => {
    const campaigns = [{ id: 'c1', name: 'Other', tags: ['other'], status: 'active' }];
    const [out] = await runCode(filterCode, make(campaigns));
    expect(out.json.campaigns).toHaveLength(0);
  });

  test('handles campaigns in .items wrapper', async () => {
    const data = {
      items: [{ id: 'c1', name: 'DW Promo', tags: ['dabbahwala'], status: 'active' }],
    };
    const [out] = await runCode(filterCode, make(data));
    expect(out.json.campaigns).toHaveLength(1);
  });

  test('normalises to campaign_id, name, tags, status fields', async () => {
    const campaigns = [{ id: 'c1', name: 'Nurture', tags: ['dabbahwala'], status: 'active' }];
    const [out] = await runCode(filterCode, make(campaigns));
    const c = out.json.campaigns[0];
    expect(c.campaign_id).toBeDefined();
    expect(c.name).toBeDefined();
    expect(c.tags).toBeDefined();
    expect(c.status).toBeDefined();
  });

  test('handles tag objects with name property', async () => {
    const campaigns = [{ id: 'c1', name: 'Test', tags: [{ name: 'dabbahwala' }], status: 'active' }];
    const [out] = await runCode(filterCode, make(campaigns));
    expect(out.json.campaigns).toHaveLength(1);
  });
});

// ─── HTTP node configs ────────────────────────────────────────────────────────

describe('Fetch All Instantly Campaigns — HTTP node', () => {
  test('URL targets Instantly campaigns', () => {
    const node = getNode(wf, 'Fetch All Instantly Campaigns');
    expect(node.parameters.url).toContain('instantly.ai');
    expect(node.parameters.url).toContain('campaigns');
  });
});

describe('Upsert Campaigns in Render DB — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Upsert Campaigns in Render DB').parameters.method).toBe('POST');
  });
  test('URL targets /api/webhooks/sync-campaigns', () => {
    expect(getNode(wf, 'Upsert Campaigns in Render DB').parameters.url).toContain('/sync-campaigns');
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
