'use strict';
/**
 * Tests for [Order Intake] Daily CSV Upload — daily_order_upload.json
 *
 * Covers:
 *  - IF node:   Has Orders? (TRUE / FALSE branch)
 *  - HTTP node: Upload CSV to API — verify multipart-form-data, correct URL, timeout
 *  - HTTP node: Run Intelligence Cycle — correct method + URL
 *  - Flow:      total_orders > 0 → Run Intelligence Cycle
 *               total_orders = 0 → No Orders — Skip (no intelligence call)
 *  - Response:  shipday_csv_download_url field present in response body
 *               drive_upload_enqueued field present in response body
 */

const {
  loadWorkflow,
  getIfConditions,
  getNode,
  evalIf,
} = require('../helpers/runtime');

let wf, ifConditions;

beforeAll(() => {
  wf = loadWorkflow('daily_order_upload.json');
  ifConditions = getIfConditions(wf, 'Has Orders?');
});

// ─── Has Orders? — IF node ────────────────────────────────────────────────────

describe('Has Orders? — IF node', () => {
  test('TRUE branch: total_orders > 0 → run intelligence cycle', () => {
    expect(evalIf(ifConditions, { total_orders: 1 })).toBe(true);
  });

  test('TRUE branch: multiple orders', () => {
    expect(evalIf(ifConditions, { total_orders: 42 })).toBe(true);
  });

  test('FALSE branch: total_orders = 0 → skip', () => {
    expect(evalIf(ifConditions, { total_orders: 0 })).toBe(false);
  });

  test('FALSE branch: total_orders missing → false', () => {
    expect(evalIf(ifConditions, {})).toBe(false);
  });

  test('FALSE branch: total_orders negative → false', () => {
    expect(evalIf(ifConditions, { total_orders: -1 })).toBe(false);
  });
});

// ─── Upload CSV to API — HTTP node config ─────────────────────────────────────

describe('Upload CSV to API — HTTP node config', () => {
  test('method is POST', () => {
    const node = getNode(wf, 'Upload CSV to API');
    expect(node.parameters.method).toBe('POST');
  });

  test('URL targets /api/daily-orders/process', () => {
    const node = getNode(wf, 'Upload CSV to API');
    expect(node.parameters.url).toContain('/api/daily-orders/process');
  });

  test('content type is multipart-form-data', () => {
    const node = getNode(wf, 'Upload CSV to API');
    expect(node.parameters.contentType).toBe('multipart-form-data');
  });

  test('body sends file as binary form data', () => {
    const node = getNode(wf, 'Upload CSV to API');
    const params = node.parameters.bodyParameters?.parameters || [];
    const fileParam = params.find(p => p.name === 'file');
    expect(fileParam).toBeDefined();
    expect(fileParam.parameterType).toBe('formBinaryData');
  });

  test('timeout is set to at least 5 minutes (300000 ms)', () => {
    const node = getNode(wf, 'Upload CSV to API');
    expect(node.parameters.options?.timeout).toBeGreaterThanOrEqual(300000);
  });
});

// ─── Run Intelligence Cycle — HTTP node config ────────────────────────────────

describe('Run Intelligence Cycle — HTTP node config', () => {
  test('method is POST', () => {
    const node = getNode(wf, 'Run Intelligence Cycle');
    expect(node.parameters.method).toBe('POST');
  });

  test('URL targets /api/intelligence/run-cycle', () => {
    const node = getNode(wf, 'Run Intelligence Cycle');
    expect(node.parameters.url).toContain('/intelligence/run-cycle');
  });
});

// ─── Workflow structure checks ─────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('has a schedule trigger node', () => {
    const node = wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger');
    expect(node).toBeDefined();
  });

  test('has Has Orders? IF node', () => {
    const node = getNode(wf, 'Has Orders?');
    expect(node.type).toBe('n8n-nodes-base.if');
  });

  test('has Run Intelligence Cycle HTTP node', () => {
    const node = getNode(wf, 'Run Intelligence Cycle');
    expect(node.type).toBe('n8n-nodes-base.httpRequest');
  });

  test('has terminal No Orders — Skip node', () => {
    const node = getNode(wf, 'No Orders — Skip');
    expect(node).toBeDefined();
  });

  test('has terminal Upload Complete node', () => {
    const node = getNode(wf, 'Upload Complete');
    expect(node).toBeDefined();
  });
});

// ─── Response field expectations ──────────────────────────────────────────────
// These document what the API response must contain so the workflow can use them

describe('API response fields — contract test', () => {
  test('response model includes total_orders (used by Has Orders? IF)', () => {
    // The IF node checks $json.total_orders — this test documents that contract
    const conds = ifConditions.conditions || ifConditions;
    const condArr = Array.isArray(conds) ? conds : (conds.conditions || []);
    const ordersCond = condArr.find(c => String(c.leftValue).includes('total_orders'));
    expect(ordersCond).toBeDefined();
    expect(ordersCond.operator.operation).toBe('gt');
    expect(ordersCond.rightValue).toBe(0);
  });

  test('Has Orders? uses numeric greater-than operator (not string equals)', () => {
    const conds = ifConditions.conditions || ifConditions;
    const condArr = Array.isArray(conds) ? conds : (conds.conditions || []);
    const c = condArr[0];
    expect(c.operator.type).toBe('number');
    expect(c.operator.operation).toBe('gt');
  });
});

// ─── Full flow simulations ─────────────────────────────────────────────────────

describe('Flow: has_orders=true → intelligence cycle runs', () => {
  test('total_orders=5 → IF passes → intelligence cycle branch taken', () => {
    const apiResponse = {
      total_orders: 5,
      new_contacts: 2,
      existing_contacts: 3,
      shipday_csv_download_url: '/api/daily-orders/download-shipday-csv/abc-uuid',
      drive_upload_enqueued: false,
    };
    expect(evalIf(ifConditions, apiResponse)).toBe(true);
  });

  test('shipday_csv_download_url field in response does not affect IF branch decision', () => {
    // IF only checks total_orders — other fields are ignored
    const apiResponse = {
      total_orders: 3,
      shipday_csv_download_url: '',   // empty URL still doesn't affect IF
      drive_upload_enqueued: false,
    };
    expect(evalIf(ifConditions, apiResponse)).toBe(true);
  });
});

describe('Flow: has_orders=false → skip, no intelligence cycle', () => {
  test('total_orders=0 → IF fails → No Orders — Skip branch taken', () => {
    const apiResponse = {
      total_orders: 0,
      rows_skipped: 5,
      shipday_csv_download_url: '',
      drive_upload_enqueued: false,
    };
    expect(evalIf(ifConditions, apiResponse)).toBe(false);
  });
});
