'use strict';
/**
 * Tests for [Email Campaigns] Bulk Seed — instantly_bulk_seed.json
 *
 * Covers:
 *  - Code: Split Contacts       — flattens array or passes through individual items
 *  - Code: Prepare Seed Payload — maps campaign, validates first_name, sets skip flag
 *  - IF:   Has First Name?      — TRUE (skip=false) / FALSE (skip=true)
 *  - HTTP: Add Lead to Instantly — POST to api.instantly.ai/api/v2/leads with Bearer
 */

const { loadWorkflow, getCodeNode, getIfConditions, getNode, runCode, evalIf } = require('../helpers/runtime');

let wf, splitCode, prepareCode, ifc;
beforeAll(() => {
  wf          = loadWorkflow('instantly_bulk_seed.json');
  splitCode   = getCodeNode(wf, 'Split Contacts');
  prepareCode = getCodeNode(wf, 'Prepare Seed Payload');
  ifc         = getIfConditions(wf, 'Has First Name?');
});

// ─── Split Contacts — Code node ───────────────────────────────────────────────

describe('Split Contacts — Code node', () => {
  test('splits wrapped array into individual items', async () => {
    const contacts = [
      { email: 'a@test.com', first_name: 'Alice', current_campaign: 'NURTURE_SLOW' },
      { email: 'b@test.com', first_name: 'Bob',   current_campaign: 'PROMO_STANDARD' },
    ];
    const items = await runCode(splitCode, { inputItems: [{ json: contacts }] });
    expect(items).toHaveLength(2);
  });

  test('returns empty array when no contacts', async () => {
    const items = await runCode(splitCode, { inputItems: [{ json: [] }] });
    expect(items).toHaveLength(0);
  });
});

// ─── Prepare Seed Payload — Code node ────────────────────────────────────────

describe('Prepare Seed Payload — Code node', () => {
  const makeItem = (overrides) => ({
    inputItems: [{
      json: {
        email: 'test@test.com',
        first_name: 'Alice',
        last_name: 'Smith',
        phone: '5551234567',
        current_campaign: 'NURTURE_SLOW',
        ...overrides,
      },
    }],
  });

  test('maps NURTURE_SLOW to correct Instantly campaign ID', async () => {
    const [out] = await runCode(prepareCode, makeItem());
    expect(out.json.skip).toBe(false);
    expect(out.json.campaign).toBe('76a88797-961a-47b6-af11-77e2211c4e73');
    expect(out.json.email).toBe('test@test.com');
    expect(out.json.first_name).toBe('Alice');
  });

  test('maps PROMO_STANDARD to correct Instantly campaign ID', async () => {
    const [out] = await runCode(prepareCode, makeItem({ current_campaign: 'PROMO_STANDARD' }));
    expect(out.json.skip).toBe(false);
    expect(out.json.campaign).toBe('f3e2d621-9bf2-4130-bc1c-f8168fc44e1e');
  });

  test('skip=true when first_name is missing', async () => {
    const [out] = await runCode(prepareCode, makeItem({ first_name: '' }));
    expect(out.json.skip).toBe(true);
    expect(out.json.reason).toContain('missing_first_name');
  });

  test('skip=true when campaign has no Instantly mapping', async () => {
    const [out] = await runCode(prepareCode, makeItem({ current_campaign: 'UNKNOWN_CAMPAIGN' }));
    expect(out.json.skip).toBe(true);
    expect(out.json.reason).toContain('no_instantly_campaign_for_');
  });

  test('maps ACTIVE_CUSTOMER to correct Instantly campaign ID', async () => {
    const [out] = await runCode(prepareCode, makeItem({ current_campaign: 'ACTIVE_CUSTOMER' }));
    expect(out.json.skip).toBe(false);
    expect(out.json.campaign).toBe('c763e229-f633-468b-bfe4-7f9a4fd21036');
  });

  test('maps REACTIVATION to correct Instantly campaign ID', async () => {
    const [out] = await runCode(prepareCode, makeItem({ current_campaign: 'REACTIVATION' }));
    expect(out.json.skip).toBe(false);
    expect(out.json.campaign).toBe('69c84455-d9b8-437f-b249-8325d23798e6');
  });
});

// ─── Has First Name? — IF node ────────────────────────────────────────────────

describe('Has First Name? — IF node', () => {
  test('TRUE (passes through): skip=false', () => {
    expect(evalIf(ifc, { skip: false })).toBe(true);
  });
  test('FALSE (skipped): skip=true', () => {
    expect(evalIf(ifc, { skip: true })).toBe(false);
  });
});

// ─── HTTP node config ─────────────────────────────────────────────────────────

describe('Add Lead to Instantly — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Add Lead to Instantly').parameters.method).toBe('POST');
  });
  test('URL targets Instantly v2 leads', () => {
    expect(getNode(wf, 'Add Lead to Instantly').parameters.url).toContain('instantly.ai');
    expect(getNode(wf, 'Add Lead to Instantly').parameters.url).toContain('leads');
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('manual trigger only (bulk seed is manual operation)', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.manualTrigger')).toBeDefined();
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeUndefined();
  });
  test('has Has First Name? IF node', () => {
    expect(getNode(wf, 'Has First Name?').type).toBe('n8n-nodes-base.if');
  });
  test('has Skipped — No First Name terminal node', () => {
    expect(getNode(wf, 'Skipped — No First Name')).toBeDefined();
  });
});
