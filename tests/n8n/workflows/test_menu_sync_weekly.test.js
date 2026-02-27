'use strict';
/**
 * Tests for [Menu] Catalog Sync (weekly scrape) — menu_sync_weekly.json
 *
 * Covers:
 *  - Code: Build Menu Summary — builds summary text from menu items
 *  - IF:   Scrape OK?         — TRUE (status=completed) / FALSE (status≠completed)
 *  - HTTP: Trigger Menu Scraper — POST to /api/menu/scrape-trigger
 *  - HTTP: Get Current Menu    — GET to /api/menu/current
 */

const { loadWorkflow, getCodeNode, getIfConditions, getNode, runCode, evalIf } = require('../helpers/runtime');

let wf, buildCode, ifc;
beforeAll(() => {
  wf        = loadWorkflow('menu_sync_weekly.json');
  buildCode = getCodeNode(wf, 'Build Menu Summary');
  ifc       = getIfConditions(wf, 'Scrape OK?');
});

// ─── Build Menu Summary — Code node ──────────────────────────────────────────

describe('Build Menu Summary — Code node', () => {
  const makeCtx = (menu) => ({
    nodeData: { 'Get Current Menu': [{ json: menu }] },
  });

  test('builds summary with week and item count', async () => {
    const menu = {
      week_start: '2026-02-01',
      item_count: 10,
      items: [
        { item_name: 'Dal Makhani', is_veg: true, is_featured: true },
        { item_name: 'Butter Chicken', is_veg: false, is_featured: false },
      ],
    };
    const [out] = await runCode(buildCode, makeCtx(menu));
    expect(out.json.summary).toContain('2026-02-01');
    expect(out.json.item_count).toBe(10);
    expect(out.json.week_start).toBe('2026-02-01');
  });

  test('separates veg and non-veg items in summary', async () => {
    const menu = {
      week_start: '2026-02-01',
      item_count: 2,
      items: [
        { item_name: 'Paneer Tikka', is_veg: true, is_featured: false },
        { item_name: 'Chicken Curry', is_veg: false, is_featured: false },
      ],
    };
    const [out] = await runCode(buildCode, makeCtx(menu));
    expect(out.json.summary).toContain('Paneer Tikka');
    expect(out.json.summary).toContain('Chicken Curry');
  });

  test('handles empty items array', async () => {
    const menu = { week_start: '2026-02-01', item_count: 0, items: [] };
    const [out] = await runCode(buildCode, makeCtx(menu));
    expect(out.json.item_count).toBe(0);
    expect(out.json.summary).toBeDefined();
  });
});

// ─── Scrape OK? — IF node ─────────────────────────────────────────────────────

describe('Scrape OK? — IF node', () => {
  test('TRUE: status=completed', () => {
    expect(evalIf(ifc, { status: 'completed' })).toBe(true);
  });
  test('FALSE: status=error', () => {
    expect(evalIf(ifc, { status: 'error' })).toBe(false);
  });
  test('FALSE: status=running', () => {
    expect(evalIf(ifc, { status: 'running' })).toBe(false);
  });
  test('FALSE: status missing', () => {
    expect(evalIf(ifc, {})).toBe(false);
  });
});

// ─── HTTP node configs ────────────────────────────────────────────────────────

describe('Trigger Menu Scraper — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Trigger Menu Scraper').parameters.method).toBe('POST');
  });
  test('URL targets /api/menu/scrape-trigger', () => {
    expect(getNode(wf, 'Trigger Menu Scraper').parameters.url).toContain('/menu/scrape-trigger');
  });
});

describe('Get Current Menu — HTTP node', () => {
  test('URL targets /api/menu/current', () => {
    expect(getNode(wf, 'Get Current Menu').parameters.url).toContain('/menu/current');
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('has a weekly schedule trigger', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeDefined();
  });
  test('has Scrape OK? IF node', () => {
    expect(getNode(wf, 'Scrape OK?').type).toBe('n8n-nodes-base.if');
  });
  test('has email nodes for both success and failure paths', () => {
    const emailNodes = wf.nodes.filter(n =>
      n.type === 'n8n-nodes-base.httpRequest' &&
      (n.parameters.url || '').includes('/send-email')
    );
    expect(emailNodes.length).toBeGreaterThanOrEqual(2);
  });
});
