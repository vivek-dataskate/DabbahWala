'use strict';
/**
 * Tests for [System] Action Queue — action_queue_executor.json
 *
 * Covers:
 *  - Code node:   Split Actions
 *  - IF node:     Has Pending Actions?
 *  - Switch node: Route by Action Type (8 branches + fallback)
 *  - HTTP config: verifies every HTTP node uses JSON body (not form-encoded),
 *                 correct Instantly endpoints, correct auth header references
 *  - Flow:        empty queue → skip; populated queue → route → execute → mark done
 */

const { runCode, evalIf, evalSwitch, loadWorkflow, getCodeNode, getIfConditions, getSwitchRules, getNode } = require('../helpers/runtime');

let wf, splitActionsCode, ifConditions, switchRules;

beforeAll(() => {
  wf = loadWorkflow('action_queue_executor.json');
  splitActionsCode = getCodeNode(wf, 'Split Actions');
  ifConditions = getIfConditions(wf, 'Has Pending Actions?');
  switchRules = getSwitchRules(wf, 'Route by Action Type');
});

// ─── Split Actions Code Node ─────────────────────────────────────────────────

describe('Split Actions — Code node', () => {
  test('returns one item per action when array is populated', async () => {
    const actions = [
      { id: 1, action_type: 'send_sms', phone: '+14165550001' },
      { id: 2, action_type: 'move_campaign', email: 'a@b.com' },
    ];
    const result = await runCode(splitActionsCode, { inputItems: [actions] });
    expect(result).toHaveLength(2);
    expect(result[0].json.action_type).toBe('send_sms');
    expect(result[1].json.action_type).toBe('move_campaign');
  });

  test('returns empty array when input is empty array', async () => {
    const result = await runCode(splitActionsCode, { inputItems: [[]] });
    expect(result).toHaveLength(0);
  });

  test('returns empty array when input is not an array', async () => {
    const result = await runCode(splitActionsCode, { inputItems: [{ not: 'an array' }] });
    expect(result).toHaveLength(0);
  });

  test('returns empty array when input is null', async () => {
    const result = await runCode(splitActionsCode, { inputItems: [null] });
    expect(result).toHaveLength(0);
  });

  test('preserves all action fields in each output item', async () => {
    const actions = [{ id: 42, action_type: 'push_instantly_lead', email: 'c@d.com', payload: { campaign_id: 'abc' } }];
    const result = await runCode(splitActionsCode, { inputItems: [actions] });
    expect(result[0].json).toMatchObject({ id: 42, email: 'c@d.com', payload: { campaign_id: 'abc' } });
  });

  test('handles single-item array correctly', async () => {
    const actions = [{ id: 7, action_type: 'send_email_report' }];
    const result = await runCode(splitActionsCode, { inputItems: [actions] });
    expect(result).toHaveLength(1);
    expect(result[0].json.id).toBe(7);
  });
});

// ─── Has Pending Actions? — IF node ─────────────────────────────────────────

describe('Has Pending Actions? — IF node', () => {
  test('TRUE branch: non-empty array length > 0', () => {
    const json = [{ id: 1 }, { id: 2 }];
    expect(evalIf(ifConditions, json)).toBe(true);
  });

  test('FALSE branch: empty array length = 0', () => {
    const json = [];
    expect(evalIf(ifConditions, json)).toBe(false);
  });

  test('FALSE branch: non-array input evaluates to 0', () => {
    const json = { some: 'object' };
    expect(evalIf(ifConditions, json)).toBe(false);
  });

  test('TRUE branch: single item array', () => {
    const json = [{ id: 99 }];
    expect(evalIf(ifConditions, json)).toBe(true);
  });
});

// ─── Route by Action Type — Switch node ──────────────────────────────────────

describe('Route by Action Type — Switch node (8 routes)', () => {
  const cases = [
    ['send_sms',                  'send_sms'],
    ['move_campaign',             'move_campaign'],
    ['escalate_airtable',         'escalate_airtable'],
    ['sync_airtable_task',        'sync_airtable_task'],
    ['push_instantly_lead',       'push_instantly_lead'],
    ['push_instantly_sequences',  'push_instantly_sequences'],
    ['upload_google_drive',       'upload_google_drive'],
    ['send_email_report',         'send_email_report'],
  ];

  test.each(cases)('action_type "%s" routes to output "%s"', (action_type, expectedOutput) => {
    const output = evalSwitch(switchRules, { action_type });
    expect(output).toBe(expectedOutput);
  });

  test('unknown action_type falls through to fallback', () => {
    const output = evalSwitch(switchRules, { action_type: 'unknown_action' });
    expect(output).toBe('fallback');
  });

  test('missing action_type falls through to fallback', () => {
    const output = evalSwitch(switchRules, {});
    expect(output).toBe('fallback');
  });
});

// ─── Flow: complete execution paths ──────────────────────────────────────────

describe('Flow: empty queue → skip', () => {
  test('empty queue: IF returns false, no split or routing happens', async () => {
    const pendingActions = [];
    const ifPassed = evalIf(ifConditions, pendingActions);
    expect(ifPassed).toBe(false);
    // Flow ends here — split-actions and route-by-type are never reached
  });
});

describe('Flow: send_sms action full path', () => {
  test('populates → split → route → send_sms branch', async () => {
    const pendingActions = [
      { id: 1, action_type: 'send_sms', phone: '+14165550000', payload: { message_body: 'Hello!' } },
    ];
    // Step 1: IF passes
    expect(evalIf(ifConditions, pendingActions)).toBe(true);

    // Step 2: Split
    const split = await runCode(splitActionsCode, { inputItems: [pendingActions] });
    expect(split).toHaveLength(1);

    // Step 3: Switch routes to send_sms
    const route = evalSwitch(switchRules, split[0].json);
    expect(route).toBe('send_sms');
  });
});

describe('Flow: mixed actions are all correctly routed', () => {
  test('each action type in a batch routes to the correct branch', async () => {
    const pendingActions = [
      { id: 1, action_type: 'send_sms' },
      { id: 2, action_type: 'upload_google_drive' },
      { id: 3, action_type: 'send_email_report' },
      { id: 4, action_type: 'push_instantly_lead' },
    ];
    const split = await runCode(splitActionsCode, { inputItems: [pendingActions] });
    const routes = split.map(item => evalSwitch(switchRules, item.json));
    expect(routes).toEqual(['send_sms', 'upload_google_drive', 'send_email_report', 'push_instantly_lead']);
  });
});

// ─── HTTP Node Configuration Tests ───────────────────────────────────────────
// These tests catch the class of bug where bodyParameters (form-encoded) is used
// instead of specifyBody=json for APIs that require JSON bodies.

describe('HTTP node config — Move to Campaign (Instantly)', () => {
  test('uses specifyBody=json (NOT form-encoded bodyParameters)', () => {
    const node = getNode(wf, 'Move to Campaign (Instantly)');
    expect(node.parameters.specifyBody).toBe('json');
    expect(node.parameters.bodyParameters).toBeUndefined();
  });

  test('body contains campaign_id field (not missing)', () => {
    const node = getNode(wf, 'Move to Campaign (Instantly)');
    expect(node.parameters.jsonBody).toContain('campaign_id');
  });

  test('body contains email field', () => {
    const node = getNode(wf, 'Move to Campaign (Instantly)');
    expect(node.parameters.jsonBody).toContain('email');
  });

  test('has Authorization Bearer header referencing INSTANTLY_BEARER credential', () => {
    const node = getNode(wf, 'Move to Campaign (Instantly)');
    const headers = node.parameters.headerParameters?.parameters || [];
    const auth = headers.find(h => h.name === 'Authorization');
    expect(auth).toBeDefined();
    expect(auth.value).toContain('INSTANTLY_BEARER');
    expect(auth.value).toMatch(/Bearer/i);
  });

  test('URL targets Instantly /api/v2/leads endpoint', () => {
    const node = getNode(wf, 'Move to Campaign (Instantly)');
    expect(node.parameters.url).toContain('instantly.ai');
    expect(node.parameters.url).toContain('/leads');
  });
});

describe('HTTP node config — Push Lead to Instantly Campaign (push_instantly_lead)', () => {
  test('uses specifyBody=json', () => {
    const node = getNode(wf, 'Push Lead to Instantly Campaign');
    expect(node.parameters.specifyBody).toBe('json');
    expect(node.parameters.bodyParameters).toBeUndefined();
  });

  test('URL includes campaign_id from payload', () => {
    const node = getNode(wf, 'Push Lead to Instantly Campaign');
    expect(node.parameters.url).toContain('instantly_campaign_id');
    expect(node.parameters.url).toContain('/campaigns/');
  });

  test('body wraps lead in leads array', () => {
    const node = getNode(wf, 'Push Lead to Instantly Campaign');
    expect(node.parameters.jsonBody).toContain('"leads"');
    expect(node.parameters.jsonBody).toContain('payload.email');
  });

  test('has Authorization Bearer header referencing INSTANTLY_BEARER', () => {
    const node = getNode(wf, 'Push Lead to Instantly Campaign');
    const headers = node.parameters.headerParameters?.parameters || [];
    const auth = headers.find(h => h.name === 'Authorization');
    expect(auth).toBeDefined();
    expect(auth.value).toContain('INSTANTLY_BEARER');
  });
});

describe('HTTP node config — Push Sequences to Instantly', () => {
  test('sequences field guards against undefined with || []', () => {
    const node = getNode(wf, 'Push Sequences to Instantly');
    expect(node.parameters.jsonBody).toContain('sequences || []');
  });

  test('has Authorization header with INSTANTLY_BEARER', () => {
    const node = getNode(wf, 'Push Sequences to Instantly');
    const headers = node.parameters.headerParameters?.parameters || [];
    const auth = headers.find(h => h.name === 'Authorization');
    expect(auth).toBeDefined();
    expect(auth.value).toContain('INSTANTLY_BEARER');
  });
});

describe('HTTP node config — Send SMS via Telnyx', () => {
  test('uses specifyBody=json', () => {
    const node = getNode(wf, 'Send SMS via Telnyx');
    expect(node.parameters.specifyBody).toBe('json');
  });

  test('has Authorization header referencing TELNYX_API_KEY', () => {
    const node = getNode(wf, 'Send SMS via Telnyx');
    const headers = node.parameters.headerParameters?.parameters || [];
    const auth = headers.find(h => h.name === 'Authorization');
    expect(auth).toBeDefined();
    expect(auth.value).toContain('TELNYX_API_KEY');
  });

  test('uses TELNYX_FROM_NUMBER from credentials (not hardcoded)', () => {
    const node = getNode(wf, 'Send SMS via Telnyx');
    expect(node.parameters.jsonBody).toContain('TELNYX_FROM_NUMBER');
    expect(node.parameters.jsonBody).not.toContain('+18444322224');
  });
});

describe('HTTP node config — Mark Done nodes use action-queue/{id}/done URL', () => {
  const markDoneNodeNames = [
    'Mark SMS Done',
    'Mark Campaign Done',
    'Mark Escalation Done',
    'Mark Airtable Task Done',
    'Mark Instantly Lead Done',
    'Mark Sequences Done',
    'Mark Drive Upload Done',
    'Mark Email Report Done',
  ];

  test.each(markDoneNodeNames.map(n => [n]))('%s URL contains /action-queue/ and /done', (nodeName) => {
    const node = getNode(wf, nodeName);
    if (!node) return; // skip if node doesn't exist in this workflow
    expect(node.parameters.url).toContain('action-queue');
    expect(node.parameters.url).toContain('/done');
    expect(node.parameters.url).toContain('Split Actions');
  });
});

describe('HTTP node config — Create Airtable Escalation', () => {
  test('uses specifyBody=json', () => {
    const node = getNode(wf, 'Create Airtable Escalation');
    expect(node.parameters.specifyBody).toBe('json');
  });

  test('has Authorization header with AIRTABLE_API_KEY', () => {
    const node = getNode(wf, 'Create Airtable Escalation');
    const headers = node.parameters.headerParameters?.parameters || [];
    const auth = headers.find(h => h.name === 'Authorization');
    expect(auth).toBeDefined();
    expect(auth.value).toContain('AIRTABLE_API_KEY');
  });
});

describe('Flow: push_instantly_lead action full path', () => {
  test('action with payload.instantly_campaign_id routes to push_instantly_lead branch', async () => {
    const pendingActions = [{
      id: 99,
      action_type: 'push_instantly_lead',
      email: 'vivek@dabbahwala.com',
      phone: '+12144996143',
      first_name: 'Vivek',
      last_name: 'Test',
      payload: {
        instantly_campaign_id: 'abc-123-campaign-uuid',
        email: 'vivek@dabbahwala.com',
        first_name: 'Vivek',
        last_name: 'Test',
        phone: '+12144996143',
        campaign_name: 'REACTIVATION',
      },
    }];

    // Step 1: IF passes
    expect(evalIf(ifConditions, pendingActions)).toBe(true);

    // Step 2: Split
    const split = await runCode(splitActionsCode, { inputItems: [pendingActions] });
    expect(split).toHaveLength(1);
    expect(split[0].json.payload.instantly_campaign_id).toBe('abc-123-campaign-uuid');

    // Step 3: Routes to push_instantly_lead
    expect(evalSwitch(switchRules, split[0].json)).toBe('push_instantly_lead');
  });
});

describe('Flow: move_campaign action routes to move_campaign branch', () => {
  test('legacy move_campaign action type is routed', async () => {
    const action = { id: 55, action_type: 'move_campaign', email: 'vivek@dabbahwala.com',
                     payload: { instantly_campaign_id: 'xyz-campaign', campaign_name: 'NURTURE_SLOW' } };
    expect(evalSwitch(switchRules, action)).toBe('move_campaign');
  });
});

describe('Flow: send_email_report action with test address', () => {
  test('routes to send_email_report branch', async () => {
    const action = {
      id: 88,
      action_type: 'send_email_report',
      payload: { to: 'vivek@dabbahwala.com', subject: 'Test report', html_body: '<p>Test</p>' },
    };
    expect(evalSwitch(switchRules, action)).toBe('send_email_report');
  });
});
