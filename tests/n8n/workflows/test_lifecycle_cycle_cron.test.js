'use strict';
/**
 * Tests for lifecycle_cycle_cron.json
 *
 * Workflow: [Intelligence] Stage Runner
 * Schedule: Every 15 minutes
 *
 * Nodes under test:
 *   IF:   "Campaigns Queued?"        — $json.campaigns_queued > 0
 *                                       TRUE  → Fetch Pending Campaigns
 *                                       FALSE → No Campaigns — Done (noOp)
 *   Code: "Split Campaign Moves"     — splits array-in-one-item vs pass-through
 *   Code: "Prepare Instantly Payload"— maps campaign names to Instantly IDs,
 *                                       handles skip cases (APP_TO_DIRECT, missing first_name),
 *                                       throws on unknown campaign name
 *   IF:   "Skip or Push?"            — $json.skip === true (boolean 'true' operation)
 *                                       TRUE  → Aggregate Skip IDs
 *                                       FALSE → Has Old Campaign?
 *   Code: "Aggregate Skip IDs"       — collects queue_ids from all items into one array
 *   Code: "Aggregate Executed IDs"   — same pattern as Aggregate Skip IDs
 *   Code: "Restore Payload"          — NO branch: passes item through; YES branch with
 *                                       _restore_failed fallback when pairing unavailable
 *   Code: "Check Push Result"        — maps Instantly response to { queue_id, success, ... }
 *   IF:   "Has Old Campaign?"        — $json.instantly_from_campaign_id notEmpty
 *                                       TRUE  → Remove from Old Campaign
 *                                       FALSE → Restore Payload
 *   IF:   "Push Succeeded?"          — $json.success === true
 *                                       TRUE  → Log Push Attempt
 *                                       FALSE → Log Failed Push
 *
 * Flow:
 *   Trigger → Get Credentials → Run Lifecycle Cycle (HTTP) → Campaigns Queued?
 *     TRUE  → Fetch Pending Campaigns → Split Campaign Moves → Prepare Instantly Payload
 *           → Skip or Push?
 *               TRUE  → Aggregate Skip IDs → Mark Skip Executed
 *               FALSE → Has Old Campaign?
 *                           TRUE  → Remove from Old Campaign → Restore Payload
 *                           FALSE → Restore Payload
 *                       → Add Lead to New Campaign → Check Push Result → Push Succeeded?
 *                           TRUE  → Log Push Attempt → Extract Executed Queue ID
 *                                 → Aggregate Executed IDs → Mark Campaign Executed
 *                           FALSE → Log Failed Push
 *     FALSE → No Campaigns — Done
 *
 * Cleanup: pure JS unit tests — no external state to clean up.
 */

const { runCode, evalIf, loadWorkflow, getCodeNode, getIfConditions } = require('../helpers/runtime');

let wf;
const code = {};
let ifCampaignsQueued;
let ifSkipOrPush;
let ifHasOldCampaign;
let ifPushSucceeded;

beforeAll(() => {
  wf = loadWorkflow('lifecycle_cycle_cron.json');

  code.splitCampaignMoves     = getCodeNode(wf, 'Split Campaign Moves');
  code.prepareInstantlyPayload = getCodeNode(wf, 'Prepare Instantly Payload');
  code.aggregateSkipIds       = getCodeNode(wf, 'Aggregate Skip IDs');
  code.aggregateExecutedIds   = getCodeNode(wf, 'Aggregate Executed IDs');
  code.restorePayload         = getCodeNode(wf, 'Restore Payload');
  code.checkPushResult        = getCodeNode(wf, 'Check Push Result');

  ifCampaignsQueued = getIfConditions(wf, 'Campaigns Queued?');
  ifSkipOrPush      = getIfConditions(wf, 'Skip or Push?');
  ifHasOldCampaign  = getIfConditions(wf, 'Has Old Campaign?');
  ifPushSucceeded   = getIfConditions(wf, 'Push Succeeded?');
});

afterAll(() => {
  wf = null;
});

// ─── Workflow metadata ────────────────────────────────────────────────────────

describe('Workflow metadata', () => {
  test('workflow name is correct', () => {
    expect(wf.name).toBe('[Intelligence] Stage Runner');
  });

  test('HTTP request calls the lifecycle run endpoint', () => {
    const http = wf.nodes.find(n => n.name === 'Run Lifecycle Cycle');
    expect(http).toBeDefined();
    expect(http.parameters.url).toBe(
      'https://dabbahwala-latest.onrender.com/api/lifecycle/run'
    );
    expect(http.parameters.method).toBe('POST');
  });

  test('workflow has a Get Credentials node', () => {
    const creds = wf.nodes.find(n => n.name === 'Get Credentials');
    expect(creds).toBeDefined();
    expect(creds.parameters.method).toBe('GET');
    expect(creds.parameters.url).toContain('/api/credentials');
  });
});

// ─── IF: Campaigns Queued? ────────────────────────────────────────────────────

describe('Campaigns Queued? — IF node', () => {
  // TRUE branch → Fetch Pending Campaigns
  test('TRUE branch: campaigns_queued=1 → proceed to fetch', () => {
    expect(evalIf(ifCampaignsQueued, { campaigns_queued: 1 })).toBe(true);
  });

  test('TRUE branch: campaigns_queued=5 → proceed to fetch', () => {
    expect(evalIf(ifCampaignsQueued, { campaigns_queued: 5 })).toBe(true);
  });

  test('TRUE branch: campaigns_queued=100 → proceed to fetch', () => {
    expect(evalIf(ifCampaignsQueued, { campaigns_queued: 100 })).toBe(true);
  });

  // FALSE branch → No Campaigns — Done
  test('FALSE branch: campaigns_queued=0 → skip (nothing queued)', () => {
    expect(evalIf(ifCampaignsQueued, { campaigns_queued: 0 })).toBe(false);
  });

  test('FALSE branch: campaigns_queued missing → skip', () => {
    expect(evalIf(ifCampaignsQueued, {})).toBe(false);
  });

  test('FALSE branch: campaigns_queued=null → skip', () => {
    expect(evalIf(ifCampaignsQueued, { campaigns_queued: null })).toBe(false);
  });

  test('FALSE branch: campaigns_queued negative → skip', () => {
    // -1 is not > 0
    expect(evalIf(ifCampaignsQueued, { campaigns_queued: -1 })).toBe(false);
  });
});

describe('Campaigns Queued? — IF node structure', () => {
  test('operator type is number', () => {
    const cond = ifCampaignsQueued.conditions[0];
    expect(cond.operator.type).toBe('number');
  });

  test('operator operation is gt (greater-than)', () => {
    const cond = ifCampaignsQueued.conditions[0];
    expect(cond.operator.operation).toBe('gt');
  });

  test('right value is 0', () => {
    const cond = ifCampaignsQueued.conditions[0];
    expect(cond.rightValue).toBe(0);
  });

  test('left value references $json.campaigns_queued', () => {
    const cond = ifCampaignsQueued.conditions[0];
    expect(cond.leftValue).toContain('campaigns_queued');
  });
});

// ─── Code: Split Campaign Moves ───────────────────────────────────────────────

describe('Split Campaign Moves — Code node', () => {
  // Case (a): one item whose .json IS the array — split it into individual items
  test('single item containing an array → splits into N individual items', async () => {
    const arr = [
      { queue_id: 1, to_campaign: 'NURTURE_SLOW' },
      { queue_id: 2, to_campaign: 'PROMO_STANDARD' },
      { queue_id: 3, to_campaign: 'ACTIVE_CUSTOMER' },
    ];
    const result = await runCode(code.splitCampaignMoves, { inputItems: [arr] });
    expect(result).toHaveLength(3);
    expect(result[0].json.queue_id).toBe(1);
    expect(result[1].json.queue_id).toBe(2);
    expect(result[2].json.queue_id).toBe(3);
  });

  test('single-item array → single item after split', async () => {
    const arr = [{ queue_id: 7, to_campaign: 'REACTIVATION' }];
    const result = await runCode(code.splitCampaignMoves, { inputItems: [arr] });
    expect(result).toHaveLength(1);
    expect(result[0].json.queue_id).toBe(7);
  });

  // Case (b): already N items — pass through unchanged
  test('multiple items already split → pass through unchanged', async () => {
    const items = [
      { json: { queue_id: 10, to_campaign: 'NURTURE_SLOW' } },
      { json: { queue_id: 11, to_campaign: 'PROMO_STANDARD' } },
    ];
    const result = await runCode(code.splitCampaignMoves, { inputItems: items });
    expect(result).toHaveLength(2);
    expect(result[0].json.queue_id).toBe(10);
    expect(result[1].json.queue_id).toBe(11);
  });

  test('single non-array item → passes through as-is', async () => {
    const item = { queue_id: 99, to_campaign: 'ACTIVE_CUSTOMER' };
    const result = await runCode(code.splitCampaignMoves, { inputItems: [item] });
    expect(result).toHaveLength(1);
    expect(result[0].json.queue_id).toBe(99);
  });

  // Edge case: empty input
  test('empty input array → returns empty array', async () => {
    const result = await runCode(code.splitCampaignMoves, { inputItems: [] });
    expect(result).toHaveLength(0);
  });

  // Edge case: single item wrapping an empty array
  test('single item containing empty array → returns empty array', async () => {
    const result = await runCode(code.splitCampaignMoves, { inputItems: [[]] });
    expect(result).toHaveLength(0);
  });

  test('preserves all fields on each split item', async () => {
    const arr = [{
      queue_id: 42,
      contact_email: 'ali@example.com',
      contact_first_name: 'Ali',
      to_campaign: 'NURTURE_SLOW',
      from_campaign: 'PROMO_STANDARD',
    }];
    const result = await runCode(code.splitCampaignMoves, { inputItems: [arr] });
    expect(result[0].json.contact_email).toBe('ali@example.com');
    expect(result[0].json.contact_first_name).toBe('Ali');
    expect(result[0].json.from_campaign).toBe('PROMO_STANDARD');
  });
});

// ─── Code: Prepare Instantly Payload ──────────────────────────────────────────

describe('Prepare Instantly Payload — Code node', () => {
  /**
   * This code node runs in mode=runOnceForEachItem so $input.item is the
   * current item. We supply it via inputItems[0].
   */
  const makeItem = (overrides = {}) => ({
    queue_id: 101,
    contact_email: 'test@example.com',
    contact_first_name: 'Priya',
    contact_last_name: 'Sharma',
    contact_phone: '+14161234567',
    to_campaign: 'NURTURE_SLOW',
    from_campaign: null,
    ...overrides,
  });

  // ── Known campaign ID mapping ────────────────────────────────────────────────

  test('NURTURE_SLOW maps to correct Instantly campaign ID', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ to_campaign: 'NURTURE_SLOW' })],
    });
    expect(result[0].json.instantly_to_campaign_id).toBe('76a88797-961a-47b6-af11-77e2211c4e73');
  });

  test('PROMO_STANDARD maps to correct Instantly campaign ID', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ to_campaign: 'PROMO_STANDARD' })],
    });
    expect(result[0].json.instantly_to_campaign_id).toBe('f3e2d621-9bf2-4130-bc1c-f8168fc44e1e');
  });

  test('ACTIVE_CUSTOMER maps to correct Instantly campaign ID', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ to_campaign: 'ACTIVE_CUSTOMER' })],
    });
    expect(result[0].json.instantly_to_campaign_id).toBe('c763e229-f633-468b-bfe4-7f9a4fd21036');
  });

  test('PROMO_AGGRESSIVE maps to correct Instantly campaign ID', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ to_campaign: 'PROMO_AGGRESSIVE' })],
    });
    expect(result[0].json.instantly_to_campaign_id).toBe('87d44ff1-8720-4c1d-92ff-b827970f323f');
  });

  test('NEW_CUSTOMER_ONBOARDING maps to correct Instantly campaign ID', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ to_campaign: 'NEW_CUSTOMER_ONBOARDING' })],
    });
    expect(result[0].json.instantly_to_campaign_id).toBe('8a5ccbfb-500d-4060-ad99-76aa0159bbf2');
  });

  test('REACTIVATION maps to correct Instantly campaign ID', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ to_campaign: 'REACTIVATION' })],
    });
    expect(result[0].json.instantly_to_campaign_id).toBe('69c84455-d9b8-437f-b249-8325d23798e6');
  });

  // ── from_campaign resolution ──────────────────────────────────────────────────

  test('from_campaign set → instantly_from_campaign_id resolved to its ID', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ from_campaign: 'PROMO_STANDARD' })],
    });
    expect(result[0].json.instantly_from_campaign_id).toBe('f3e2d621-9bf2-4130-bc1c-f8168fc44e1e');
  });

  test('from_campaign=null → instantly_from_campaign_id is null', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ from_campaign: null })],
    });
    expect(result[0].json.instantly_from_campaign_id).toBeNull();
  });

  test('from_campaign not provided → instantly_from_campaign_id is null', async () => {
    const item = makeItem();
    delete item.from_campaign;
    const result = await runCode(code.prepareInstantlyPayload, { inputItems: [item] });
    expect(result[0].json.instantly_from_campaign_id).toBeNull();
  });

  // ── Payload fields ────────────────────────────────────────────────────────────

  test('output includes queue_id, email, first_name, last_name, phone', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem()],
    });
    const out = result[0].json;
    expect(out.queue_id).toBe(101);
    expect(out.email).toBe('test@example.com');
    expect(out.first_name).toBe('Priya');
    expect(out.last_name).toBe('Sharma');
    expect(out.phone).toBe('+14161234567');
  });

  test('skip is false for a normal item', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem()],
    });
    expect(result[0].json.skip).toBe(false);
  });

  test('to_campaign name is preserved in output', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ to_campaign: 'ACTIVE_CUSTOMER' })],
    });
    expect(result[0].json.to_campaign).toBe('ACTIVE_CUSTOMER');
  });

  test('contact_phone=null → phone is null in output', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ contact_phone: null })],
    });
    expect(result[0].json.phone).toBeNull();
  });

  test('first_name is trimmed from contact_first_name', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ contact_first_name: '  Ali  ' })],
    });
    expect(result[0].json.first_name).toBe('Ali');
  });

  test('last_name is trimmed from contact_last_name', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ contact_last_name: '  Khan  ' })],
    });
    expect(result[0].json.last_name).toBe('Khan');
  });

  test('missing contact_last_name → last_name is empty string', async () => {
    const item = makeItem();
    delete item.contact_last_name;
    const result = await runCode(code.prepareInstantlyPayload, { inputItems: [item] });
    expect(result[0].json.last_name).toBe('');
  });

  // ── Skip: APP_TO_DIRECT ───────────────────────────────────────────────────────

  test('APP_TO_DIRECT → skip=true with reason', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ to_campaign: 'APP_TO_DIRECT' })],
    });
    const out = result[0].json;
    expect(out.skip).toBe(true);
    expect(out.reason).toContain('APP_TO_DIRECT');
    expect(out.queue_id).toBe(101);
    expect(out.email).toBe('test@example.com');
  });

  test('APP_TO_DIRECT → no instantly_to_campaign_id in output', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ to_campaign: 'APP_TO_DIRECT' })],
    });
    expect(result[0].json.instantly_to_campaign_id).toBeUndefined();
  });

  // ── Skip: missing first_name ──────────────────────────────────────────────────

  test('missing contact_first_name → skip=true, reason=missing_first_name', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ contact_first_name: '' })],
    });
    const out = result[0].json;
    expect(out.skip).toBe(true);
    expect(out.reason).toBe('missing_first_name');
    expect(out.queue_id).toBe(101);
  });

  test('contact_first_name whitespace-only → skip=true, reason=missing_first_name', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ contact_first_name: '   ' })],
    });
    expect(result[0].json.skip).toBe(true);
    expect(result[0].json.reason).toBe('missing_first_name');
  });

  test('contact_first_name null → skip=true, reason=missing_first_name', async () => {
    const result = await runCode(code.prepareInstantlyPayload, {
      inputItems: [makeItem({ contact_first_name: null })],
    });
    expect(result[0].json.skip).toBe(true);
    expect(result[0].json.reason).toBe('missing_first_name');
  });

  // ── Edge case: unknown campaign name → throws ─────────────────────────────────

  test('unknown campaign name → throws an error', async () => {
    await expect(
      runCode(code.prepareInstantlyPayload, {
        inputItems: [makeItem({ to_campaign: 'TOTALLY_UNKNOWN_CAMPAIGN' })],
      })
    ).rejects.toThrow('Unknown campaign name: TOTALLY_UNKNOWN_CAMPAIGN');
  });

  test('empty string campaign name → throws an error', async () => {
    await expect(
      runCode(code.prepareInstantlyPayload, {
        inputItems: [makeItem({ to_campaign: '' })],
      })
    ).rejects.toThrow('Unknown campaign name:');
  });
});

// ─── IF: Skip or Push? ────────────────────────────────────────────────────────

describe('Skip or Push? — IF node', () => {
  // TRUE branch → Aggregate Skip IDs
  test('TRUE branch: skip=true → aggregate and mark skipped', () => {
    expect(evalIf(ifSkipOrPush, { skip: true })).toBe(true);
  });

  // FALSE branch → Has Old Campaign?
  test('FALSE branch: skip=false → proceed to push', () => {
    expect(evalIf(ifSkipOrPush, { skip: false })).toBe(false);
  });

  test('FALSE branch: skip missing → proceed to push', () => {
    expect(evalIf(ifSkipOrPush, {})).toBe(false);
  });

  test('FALSE branch: skip=null → proceed to push', () => {
    expect(evalIf(ifSkipOrPush, { skip: null })).toBe(false);
  });

  test('FALSE branch: skip="true" (string) does NOT match boolean true', () => {
    // operator is type=boolean, operation=true which checks left === true strictly
    expect(evalIf(ifSkipOrPush, { skip: 'true' })).toBe(false);
  });

  test('FALSE branch: skip=1 (number) does NOT match boolean true', () => {
    expect(evalIf(ifSkipOrPush, { skip: 1 })).toBe(false);
  });
});

describe('Skip or Push? — IF node structure', () => {
  test('operator type is boolean', () => {
    const cond = ifSkipOrPush.conditions[0];
    expect(cond.operator.type).toBe('boolean');
  });

  test('operator operation is "true"', () => {
    const cond = ifSkipOrPush.conditions[0];
    expect(cond.operator.operation).toBe('true');
  });

  test('left value references $json.skip', () => {
    const cond = ifSkipOrPush.conditions[0];
    expect(cond.leftValue).toContain('$json.skip');
  });
});

// ─── Code: Aggregate Skip IDs ─────────────────────────────────────────────────

describe('Aggregate Skip IDs — Code node', () => {
  test('single item → queue_ids array with one ID', async () => {
    const result = await runCode(code.aggregateSkipIds, {
      inputItems: [{ queue_id: 42, skip: true }],
    });
    expect(result).toHaveLength(1);
    expect(result[0].json.queue_ids).toEqual([42]);
  });

  test('multiple items → queue_ids collected into single array', async () => {
    const items = [
      { queue_id: 1, skip: true },
      { queue_id: 2, skip: true },
      { queue_id: 3, skip: true },
    ];
    const result = await runCode(code.aggregateSkipIds, { inputItems: items });
    expect(result).toHaveLength(1);
    expect(result[0].json.queue_ids).toEqual([1, 2, 3]);
  });

  test('filters out undefined queue_ids', async () => {
    const items = [
      { queue_id: 10 },
      { other_field: 'no_id' },
      { queue_id: 20 },
    ];
    const result = await runCode(code.aggregateSkipIds, { inputItems: items });
    expect(result[0].json.queue_ids).toEqual([10, 20]);
  });

  test('filters out null queue_ids', async () => {
    const items = [
      { queue_id: 5 },
      { queue_id: null },
      { queue_id: 6 },
    ];
    const result = await runCode(code.aggregateSkipIds, { inputItems: items });
    expect(result[0].json.queue_ids).toEqual([5, 6]);
  });

  test('all null queue_ids → empty array', async () => {
    const items = [{ queue_id: null }, { queue_id: null }];
    const result = await runCode(code.aggregateSkipIds, { inputItems: items });
    expect(result[0].json.queue_ids).toEqual([]);
  });

  test('empty input → queue_ids is empty array', async () => {
    const result = await runCode(code.aggregateSkipIds, { inputItems: [] });
    expect(result[0].json.queue_ids).toEqual([]);
  });

  test('output is always exactly one item', async () => {
    const items = Array.from({ length: 10 }, (_, i) => ({ queue_id: i + 1 }));
    const result = await runCode(code.aggregateSkipIds, { inputItems: items });
    expect(result).toHaveLength(1);
  });

  test('preserves string queue_ids', async () => {
    const items = [{ queue_id: 'uuid-abc' }, { queue_id: 'uuid-def' }];
    const result = await runCode(code.aggregateSkipIds, { inputItems: items });
    expect(result[0].json.queue_ids).toEqual(['uuid-abc', 'uuid-def']);
  });
});

// ─── Code: Aggregate Executed IDs ────────────────────────────────────────────

describe('Aggregate Executed IDs — Code node', () => {
  test('single item → queue_ids array with one ID', async () => {
    const result = await runCode(code.aggregateExecutedIds, {
      inputItems: [{ queue_id: 55 }],
    });
    expect(result[0].json.queue_ids).toEqual([55]);
  });

  test('multiple items → all queue_ids collected', async () => {
    const items = [
      { queue_id: 100 },
      { queue_id: 101 },
      { queue_id: 102 },
    ];
    const result = await runCode(code.aggregateExecutedIds, { inputItems: items });
    expect(result[0].json.queue_ids).toEqual([100, 101, 102]);
  });

  test('filters out null and undefined queue_ids', async () => {
    const items = [
      { queue_id: 200 },
      { queue_id: null },
      { queue_id: undefined },
      { queue_id: 201 },
    ];
    const result = await runCode(code.aggregateExecutedIds, { inputItems: items });
    expect(result[0].json.queue_ids).toEqual([200, 201]);
  });

  test('empty input → queue_ids is empty array', async () => {
    const result = await runCode(code.aggregateExecutedIds, { inputItems: [] });
    expect(result[0].json.queue_ids).toEqual([]);
  });

  test('output is always exactly one item', async () => {
    const items = Array.from({ length: 5 }, (_, i) => ({ queue_id: i + 50 }));
    const result = await runCode(code.aggregateExecutedIds, { inputItems: items });
    expect(result).toHaveLength(1);
  });

  test('Aggregate Executed IDs and Aggregate Skip IDs produce the same structure', async () => {
    const items = [{ queue_id: 1 }, { queue_id: 2 }];
    const skipResult = await runCode(code.aggregateSkipIds, { inputItems: items });
    const execResult = await runCode(code.aggregateExecutedIds, { inputItems: items });
    // Both should have the same output shape
    expect(Object.keys(skipResult[0].json)).toEqual(Object.keys(execResult[0].json));
    expect(skipResult[0].json.queue_ids).toEqual(execResult[0].json.queue_ids);
  });
});

// ─── IF: Has Old Campaign? ────────────────────────────────────────────────────

describe('Has Old Campaign? — IF node', () => {
  // TRUE branch → Remove from Old Campaign
  test('TRUE branch: instantly_from_campaign_id set → remove from old campaign', () => {
    expect(evalIf(ifHasOldCampaign, {
      instantly_from_campaign_id: 'f3e2d621-9bf2-4130-bc1c-f8168fc44e1e',
    })).toBe(true);
  });

  test('TRUE branch: any non-empty UUID → TRUE', () => {
    expect(evalIf(ifHasOldCampaign, {
      instantly_from_campaign_id: '76a88797-961a-47b6-af11-77e2211c4e73',
    })).toBe(true);
  });

  // FALSE branch → Restore Payload directly
  test('FALSE branch: instantly_from_campaign_id=null → skip removal', () => {
    expect(evalIf(ifHasOldCampaign, { instantly_from_campaign_id: null })).toBe(false);
  });

  test('FALSE branch: instantly_from_campaign_id="" (empty string) → skip removal', () => {
    expect(evalIf(ifHasOldCampaign, { instantly_from_campaign_id: '' })).toBe(false);
  });

  test('FALSE branch: instantly_from_campaign_id missing → skip removal', () => {
    expect(evalIf(ifHasOldCampaign, {})).toBe(false);
  });
});

describe('Has Old Campaign? — IF node structure', () => {
  test('operator operation is notEmpty', () => {
    const cond = ifHasOldCampaign.conditions[0];
    expect(cond.operator.operation).toBe('notEmpty');
  });

  test('left value references $json.instantly_from_campaign_id', () => {
    const cond = ifHasOldCampaign.conditions[0];
    expect(cond.leftValue).toContain('instantly_from_campaign_id');
  });
});

// ─── Code: Restore Payload ────────────────────────────────────────────────────

describe('Restore Payload — Code node (NO branch — item has queue_id)', () => {
  /**
   * NO branch (from Has Old Campaign? FALSE output): $input.item.json has
   * the original payload fields including queue_id.
   */
  test('NO branch: item with queue_id → passes through unchanged', async () => {
    const payload = {
      queue_id: 77,
      email: 'user@example.com',
      first_name: 'Raj',
      instantly_to_campaign_id: 'c763e229-f633-468b-bfe4-7f9a4fd21036',
      skip: false,
    };
    const result = await runCode(code.restorePayload, { inputItems: [payload] });
    expect(result[0].json.queue_id).toBe(77);
    expect(result[0].json.email).toBe('user@example.com');
    expect(result[0].json.first_name).toBe('Raj');
  });

  test('NO branch: all payload fields are preserved', async () => {
    const payload = {
      queue_id: 88,
      email: 'b@b.com',
      first_name: 'B',
      last_name: 'C',
      phone: '+1234567890',
      to_campaign: 'NURTURE_SLOW',
      from_campaign: null,
      instantly_to_campaign_id: '76a88797-961a-47b6-af11-77e2211c4e73',
      instantly_from_campaign_id: null,
      skip: false,
    };
    const result = await runCode(code.restorePayload, { inputItems: [payload] });
    expect(result[0].json).toMatchObject(payload);
  });
});

describe('Restore Payload — Code node (YES branch — pairing unavailable)', () => {
  /**
   * YES branch (from Remove from Old Campaign): $input.item.json is the
   * Instantly DELETE response, NOT the original payload. The code tries
   * $('Prepare Instantly Payload').item.json but that will throw in our
   * sandbox (no pairing). It falls back to { queue_id: null, ... _restore_failed: true }.
   */
  test('YES branch with nodeData: Instantly DELETE response → restores original payload', async () => {
    // Simulate YES branch: item has no queue_id (Instantly DELETE response)
    // but nodeData provides the original Prepare Instantly Payload data
    const instantlyDeleteResponse = { status: 200, email: 'user@example.com' };
    const originalPayload = { queue_id: 42, email: 'original@example.com', to_campaign: 'PROMO_STANDARD' };
    const result = await runCode(code.restorePayload, {
      inputItems: [instantlyDeleteResponse],
      nodeData: { 'Prepare Instantly Payload': [originalPayload] },
    });
    // $('Prepare Instantly Payload').item.json returns the original payload
    expect(result[0].json.queue_id).toBe(42);
    expect(result[0].json.email).toBe('original@example.com');
  });

  test('YES branch with nodeData: all original payload fields are restored', async () => {
    const deleteResponse = { email: 'kept@example.com' };
    const originalPayload = { queue_id: 99, email: 'kept@example.com', to_campaign: 'NURTURE_SLOW', skip: false };
    const result = await runCode(code.restorePayload, {
      inputItems: [deleteResponse],
      nodeData: { 'Prepare Instantly Payload': [originalPayload] },
    });
    const out = result[0].json;
    expect(out.queue_id).toBe(99);
    expect(out.to_campaign).toBe('NURTURE_SLOW');
    expect(out.skip).toBe(false);
  });
});

// ─── Code: Check Push Result ──────────────────────────────────────────────────

describe('Check Push Result — Code node', () => {
  /**
   * This node runs runOnceForEachItem. It attempts $('Prepare Instantly Payload').item.json
   * for queue_id/email/to_campaign. In our sandbox that $(...) call throws, so we
   * also provide fallback paths via the item's own fields (item.queue_id, item.email).
   *
   * We test the branch where pairing fails (most common in unit tests) and the
   * node falls back to item.queue_id / item.email.
   */

  test('successful push (no error, no statusCode>=400) → success=true', async () => {
    // Instantly returns something like { id: 'lead-uuid', email: 'x@y.com' }
    const instantlyResponse = { id: 'lead-uuid', email: 'x@y.com', queue_id: 200 };
    const result = await runCode(code.checkPushResult, {
      inputItems: [instantlyResponse],
    });
    expect(result[0].json.success).toBe(true);
  });

  test('Instantly error field present → success=false', async () => {
    const instantlyResponse = {
      error: { message: 'Lead already exists' },
      queue_id: 201,
    };
    const result = await runCode(code.checkPushResult, {
      inputItems: [instantlyResponse],
    });
    expect(result[0].json.success).toBe(false);
  });

  test('statusCode=400 → success=false', async () => {
    const instantlyResponse = { statusCode: 400, message: 'Bad request', queue_id: 202 };
    const result = await runCode(code.checkPushResult, {
      inputItems: [instantlyResponse],
    });
    expect(result[0].json.success).toBe(false);
  });

  test('statusCode=500 → success=false', async () => {
    const instantlyResponse = { statusCode: 500, message: 'Internal error', queue_id: 203 };
    const result = await runCode(code.checkPushResult, {
      inputItems: [instantlyResponse],
    });
    expect(result[0].json.success).toBe(false);
  });

  test('statusCode=200 → success=true', async () => {
    const instantlyResponse = { statusCode: 200, queue_id: 204 };
    const result = await runCode(code.checkPushResult, {
      inputItems: [instantlyResponse],
    });
    expect(result[0].json.success).toBe(true);
  });

  test('error_message is set when hasError=true', async () => {
    const instantlyResponse = {
      error: { message: 'Rate limit exceeded' },
      queue_id: 205,
    };
    const result = await runCode(code.checkPushResult, {
      inputItems: [instantlyResponse],
    });
    expect(result[0].json.error_message).toContain('Rate limit exceeded');
  });

  test('error_message is null on success', async () => {
    const instantlyResponse = { id: 'ok-lead', queue_id: 206 };
    const result = await runCode(code.checkPushResult, {
      inputItems: [instantlyResponse],
    });
    expect(result[0].json.error_message).toBeNull();
  });

  test('response_body is a JSON string of the input item', async () => {
    const instantlyResponse = { id: 'resp-check', queue_id: 207 };
    const result = await runCode(code.checkPushResult, {
      inputItems: [instantlyResponse],
    });
    expect(typeof result[0].json.response_body).toBe('string');
  });

  test('output always has required fields: queue_id, email, to_campaign, success, status_code, error_message, response_body', async () => {
    const result = await runCode(code.checkPushResult, {
      inputItems: [{ queue_id: 208 }],
    });
    const out = result[0].json;
    expect(out).toHaveProperty('queue_id');
    expect(out).toHaveProperty('email');
    expect(out).toHaveProperty('to_campaign');
    expect(out).toHaveProperty('success');
    expect(out).toHaveProperty('status_code');
    expect(out).toHaveProperty('error_message');
    expect(out).toHaveProperty('response_body');
  });

  test('queue_id comes from Prepare Instantly Payload nodeData', async () => {
    // When nodeData is provided, queue_id/email/to_campaign come from prepared payload
    const instantlyResponse = { id: 'lead-uuid' };
    const result = await runCode(code.checkPushResult, {
      inputItems: [instantlyResponse],
      nodeData: { 'Prepare Instantly Payload': [{ queue_id: 999, email: 'fb@example.com', to_campaign: 'NURTURE_SLOW' }] },
    });
    expect(result[0].json.queue_id).toBe(999);
    expect(result[0].json.email).toBe('fb@example.com');
  });
});

// ─── IF: Push Succeeded? ──────────────────────────────────────────────────────

describe('Push Succeeded? — IF node', () => {
  // TRUE branch → Log Push Attempt → Extract Executed Queue ID
  test('TRUE branch: success=true → log and mark executed', () => {
    expect(evalIf(ifPushSucceeded, { success: true })).toBe(true);
  });

  // FALSE branch → Log Failed Push
  test('FALSE branch: success=false → log failure only', () => {
    expect(evalIf(ifPushSucceeded, { success: false })).toBe(false);
  });

  test('FALSE branch: success missing → treat as failure', () => {
    expect(evalIf(ifPushSucceeded, {})).toBe(false);
  });

  test('FALSE branch: success=null → treat as failure', () => {
    expect(evalIf(ifPushSucceeded, { success: null })).toBe(false);
  });

  test('FALSE branch: success="true" (string) does NOT match boolean true', () => {
    // The operator uses type=boolean, operation=equals, rightValue=true
    expect(evalIf(ifPushSucceeded, { success: 'true' })).toBe(false);
  });
});

describe('Push Succeeded? — IF node structure', () => {
  test('operator type is boolean', () => {
    const cond = ifPushSucceeded.conditions[0];
    expect(cond.operator.type).toBe('boolean');
  });

  test('operator operation is equals', () => {
    const cond = ifPushSucceeded.conditions[0];
    expect(cond.operator.operation).toBe('equals');
  });

  test('right value is true', () => {
    const cond = ifPushSucceeded.conditions[0];
    expect(cond.rightValue).toBe(true);
  });

  test('left value references $json.success', () => {
    const cond = ifPushSucceeded.conditions[0];
    expect(cond.leftValue).toContain('$json.success');
  });
});

// ─── Flow integration ─────────────────────────────────────────────────────────

describe('Flow: no campaigns queued → early exit', () => {
  test('campaigns_queued=0 → IF FALSE → no further processing', () => {
    expect(evalIf(ifCampaignsQueued, { campaigns_queued: 0 })).toBe(false);
  });

  test('lifecycle API error response → campaigns_queued missing → IF FALSE', () => {
    expect(evalIf(ifCampaignsQueued, { error: 'db timeout' })).toBe(false);
  });
});

describe('Flow: campaigns queued → split → prepare → skip path', () => {
  test('campaigns_queued=2 → IF TRUE → proceeds', () => {
    expect(evalIf(ifCampaignsQueued, { campaigns_queued: 2 })).toBe(true);
  });

  test('array-in-one-item → split produces individual items', async () => {
    const arr = [
      { queue_id: 1, to_campaign: 'NURTURE_SLOW', contact_email: 'a@a.com' },
      { queue_id: 2, to_campaign: 'PROMO_STANDARD', contact_email: 'b@b.com' },
    ];
    const split = await runCode(code.splitCampaignMoves, { inputItems: [arr] });
    expect(split).toHaveLength(2);
  });

  test('APP_TO_DIRECT campaign → prepare returns skip=true → Skip or Push? routes to skip', async () => {
    const item = {
      queue_id: 10,
      contact_email: 'app@example.com',
      contact_first_name: 'App',
      to_campaign: 'APP_TO_DIRECT',
      from_campaign: null,
    };
    const prepared = await runCode(code.prepareInstantlyPayload, { inputItems: [item] });
    expect(prepared[0].json.skip).toBe(true);
    expect(evalIf(ifSkipOrPush, prepared[0].json)).toBe(true);
  });

  test('missing first_name → prepare returns skip=true → Skip or Push? routes to skip', async () => {
    const item = {
      queue_id: 11,
      contact_email: 'nofirst@example.com',
      contact_first_name: '',
      to_campaign: 'NURTURE_SLOW',
      from_campaign: null,
    };
    const prepared = await runCode(code.prepareInstantlyPayload, { inputItems: [item] });
    expect(prepared[0].json.skip).toBe(true);
    expect(evalIf(ifSkipOrPush, prepared[0].json)).toBe(true);
  });

  test('skipped items → Aggregate Skip IDs collects queue_ids for bulk mark', async () => {
    const skipItems = [
      { queue_id: 10, skip: true, reason: 'APP_TO_DIRECT' },
      { queue_id: 11, skip: true, reason: 'missing_first_name' },
    ];
    const agg = await runCode(code.aggregateSkipIds, { inputItems: skipItems });
    expect(agg[0].json.queue_ids).toEqual([10, 11]);
  });
});

describe('Flow: campaigns queued → prepare → push path', () => {
  test('normal item → prepare returns skip=false → Skip or Push? routes to push', async () => {
    const item = {
      queue_id: 20,
      contact_email: 'real@example.com',
      contact_first_name: 'Real',
      contact_last_name: 'Person',
      contact_phone: '+1234567890',
      to_campaign: 'ACTIVE_CUSTOMER',
      from_campaign: null,
    };
    const prepared = await runCode(code.prepareInstantlyPayload, { inputItems: [item] });
    expect(prepared[0].json.skip).toBe(false);
    expect(evalIf(ifSkipOrPush, prepared[0].json)).toBe(false);
  });

  test('item with from_campaign → Has Old Campaign? routes TRUE', async () => {
    const preparedItem = {
      queue_id: 20,
      instantly_from_campaign_id: 'f3e2d621-9bf2-4130-bc1c-f8168fc44e1e',
      skip: false,
    };
    expect(evalIf(ifHasOldCampaign, preparedItem)).toBe(true);
  });

  test('item without from_campaign → Has Old Campaign? routes FALSE', async () => {
    const preparedItem = {
      queue_id: 21,
      instantly_from_campaign_id: null,
      skip: false,
    };
    expect(evalIf(ifHasOldCampaign, preparedItem)).toBe(false);
  });

  test('successful push → Check Push Result success=true → Push Succeeded? TRUE', async () => {
    const instantlyResp = { id: 'new-lead', queue_id: 22 };
    const checked = await runCode(code.checkPushResult, { inputItems: [instantlyResp] });
    expect(checked[0].json.success).toBe(true);
    expect(evalIf(ifPushSucceeded, checked[0].json)).toBe(true);
  });

  test('failed push → Check Push Result success=false → Push Succeeded? FALSE', async () => {
    const instantlyResp = { statusCode: 422, error: { message: 'Unprocessable' }, queue_id: 23 };
    const checked = await runCode(code.checkPushResult, { inputItems: [instantlyResp] });
    expect(checked[0].json.success).toBe(false);
    expect(evalIf(ifPushSucceeded, checked[0].json)).toBe(false);
  });

  test('successful leads → Aggregate Executed IDs collects queue_ids', async () => {
    const executedItems = [
      { queue_id: 22 },
      { queue_id: 23 },
      { queue_id: 24 },
    ];
    const agg = await runCode(code.aggregateExecutedIds, { inputItems: executedItems });
    expect(agg[0].json.queue_ids).toEqual([22, 23, 24]);
  });
});

describe('Flow: full happy-path — array response, normal item, successful push', () => {
  test('end-to-end: split → prepare → push-path → aggregate executed IDs', async () => {
    // Step 1: campaigns_queued=1 → IF TRUE
    expect(evalIf(ifCampaignsQueued, { campaigns_queued: 1 })).toBe(true);

    // Step 2: split array-in-one-item
    const arr = [{
      queue_id: 500,
      contact_email: 'full@example.com',
      contact_first_name: 'Full',
      contact_last_name: 'Path',
      contact_phone: null,
      to_campaign: 'NURTURE_SLOW',
      from_campaign: null,
    }];
    const split = await runCode(code.splitCampaignMoves, { inputItems: [arr] });
    expect(split).toHaveLength(1);

    // Step 3: prepare payload
    const prepared = await runCode(code.prepareInstantlyPayload, { inputItems: [split[0].json] });
    const prep = prepared[0].json;
    expect(prep.skip).toBe(false);
    expect(prep.instantly_to_campaign_id).toBe('76a88797-961a-47b6-af11-77e2211c4e73');

    // Step 4: Skip or Push? → FALSE (go to push)
    expect(evalIf(ifSkipOrPush, prep)).toBe(false);

    // Step 5: Has Old Campaign? → FALSE (no from_campaign)
    expect(evalIf(ifHasOldCampaign, prep)).toBe(false);

    // Step 6: Instantly push succeeds
    const instantlyResp = { id: 'lead-ok', queue_id: 500 };
    const checked = await runCode(code.checkPushResult, { inputItems: [instantlyResp] });
    expect(checked[0].json.success).toBe(true);

    // Step 7: Push Succeeded? → TRUE
    expect(evalIf(ifPushSucceeded, checked[0].json)).toBe(true);

    // Step 8: Aggregate Executed IDs
    const agg = await runCode(code.aggregateExecutedIds, { inputItems: [{ queue_id: 500 }] });
    expect(agg[0].json.queue_ids).toEqual([500]);
  });
});
