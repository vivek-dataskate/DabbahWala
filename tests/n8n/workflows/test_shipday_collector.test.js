'use strict';
/**
 * Tests for [Order Intake] Order Collector — shipday_delivery_collector.json
 *
 * Covers:
 *  Code: Build Time Window, Normalise Orders
 *  IF:   Has Contact?, Order Delivered?
 */

const { runCode, evalIf, loadWorkflow, getCodeNode, getIfConditions } = require('../helpers/runtime');

let wf;
const code = {};
const ifs  = {};

beforeAll(() => {
  wf = loadWorkflow('shipday_delivery_collector.json');
  code.buildWindow  = getCodeNode(wf, 'Build Time Window');
  code.normalise    = getCodeNode(wf, 'Normalise Orders');
  ifs.hasContact    = getIfConditions(wf, 'Has Contact?');
  ifs.orderDelivered = getIfConditions(wf, 'Order Delivered?');
});

// ─── Build Time Window ────────────────────────────────────────────────────────

describe('Build Time Window — Code node', () => {
  test('returns window_from and window_from_iso', async () => {
    const result = await runCode(code.buildWindow);
    const out = result[0].json;
    expect(out).toHaveProperty('window_from');
    expect(out).toHaveProperty('window_from_iso');
  });

  test('window_from is in YYYY-MM-DD HH:MM:SS format', async () => {
    const result = await runCode(code.buildWindow);
    expect(result[0].json.window_from).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
  });

  test('window_from_iso is a valid ISO date string', async () => {
    const result = await runCode(code.buildWindow);
    const d = new Date(result[0].json.window_from_iso);
    expect(d.getTime()).not.toBeNaN();
  });

  test('window is ~35 minutes before now', async () => {
    const before = Date.now();
    const result = await runCode(code.buildWindow);
    const after = Date.now();
    const windowMs = new Date(result[0].json.window_from_iso).getTime();
    const expectedMs = (before + after) / 2 - 35 * 60 * 1000;
    expect(Math.abs(windowMs - expectedMs)).toBeLessThan(5000);
  });
});

// ─── Normalise Orders ─────────────────────────────────────────────────────────

describe('Normalise Orders — Code node', () => {
  // makeOrderData: raw Shipday order object (inner item)
  const makeOrderData = (status, email = 'cust@dabbahwala.com', phone = '+14165550001') => ({
    orderId: 'ORD-001',
    orderNumber: 'REF-001',
    orderStatus: status,
    customer: { email, phone, name: 'Test Customer', address: '123 Main St' },
    assignedCarrier: { name: 'Ali Driver', phone: '+14165559999' },
    estimatedDeliveryTime: '2024-01-01T12:00:00Z',
    actualDeliveryTime: null,
  });
  // makeOrder: Shipday API response wrapper — the shape $input.item.json has
  const makeOrder = (status, email, phone) => ({ orders: [makeOrderData(status, email, phone)] });

  const STATUS_CASES = [
    ['ACCEPTED',   'order_accepted',    'accepted'],
    ['ASSIGNED',   'driver_assigned',   'driver_assigned'],
    ['PICKED_UP',  'out_for_delivery',  'out_for_delivery'],
    ['ARRIVED',    'delivery_arrived',  'arrived'],
    ['COMPLETED',  'delivered',         'delivered'],
    ['FAILED',     'delivery_failed',   'failed'],
    ['RETURNED',   'delivery_returned', 'returned'],
    ['UNASSIGNED', 'delivery_pending',  'pending'],
  ];

  test.each(STATUS_CASES)('Shipday status %s → event_type "%s", delivery_status "%s"', async (status, expectedEvent, expectedDeliveryStatus) => {
    const result = await runCode(code.normalise, { inputItems: [makeOrder(status)] });
    expect(result).toHaveLength(1);
    expect(result[0].json.event_type).toBe(expectedEvent);
    expect(result[0].json.delivery_status).toBe(expectedDeliveryStatus);
    expect(result[0].json.shipday_status).toBe(status);
  });

  test('filters out orders with no email and no phone', async () => {
    const result = await runCode(code.normalise, { inputItems: [makeOrder('COMPLETED', '', '')] });
    expect(result).toHaveLength(0);
  });

  test('accepts order with only email (no phone)', async () => {
    const result = await runCode(code.normalise, { inputItems: [makeOrder('COMPLETED', 'cust@dabbahwala.com', '')] });
    expect(result).toHaveLength(1);
    expect(result[0].json.contact_email).toBe('cust@dabbahwala.com');
  });

  test('accepts order with only phone (no email)', async () => {
    const result = await runCode(code.normalise, { inputItems: [makeOrder('COMPLETED', '', '+14165550001')] });
    expect(result).toHaveLength(1);
    expect(result[0].json.contact_phone).toBe('+14165550001');
  });

  test('handles flat array response shape', async () => {
    // Code node also accepts Array.isArray(raw) format
    const result = await runCode(code.normalise, { inputItems: [[makeOrderData('COMPLETED')]] });
    expect(result).toHaveLength(1);
  });

  test('handles unknown status → generic delivery_update event', async () => {
    const result = await runCode(code.normalise, { inputItems: [makeOrder('SOME_UNKNOWN_STATUS')] });
    expect(result).toHaveLength(1);
    expect(result[0].json.event_type).toBe('delivery_update');
  });

  test('includes order metadata', async () => {
    const result = await runCode(code.normalise, { inputItems: [makeOrder('COMPLETED')] });
    expect(result[0].json.metadata).toHaveProperty('shipday_order_id');
    expect(result[0].json.metadata).toHaveProperty('driver');
  });

  test('driver_name is empty string when no carrier assigned', async () => {
    const data = makeOrderData('ACCEPTED');
    data.assignedCarrier = null;
    const result = await runCode(code.normalise, { inputItems: [{ orders: [data] }] });
    expect(result[0].json.driver_name).toBe('');
  });
});

// ─── Has Contact? — IF node ───────────────────────────────────────────────────

describe('Has Contact? — IF node', () => {
  test('TRUE: contact_email present', () => {
    expect(evalIf(ifs.hasContact, { contact_email: 'a@b.com', contact_phone: '' })).toBe(true);
  });

  test('TRUE: contact_phone present', () => {
    expect(evalIf(ifs.hasContact, { contact_email: '', contact_phone: '+14165550001' })).toBe(true);
  });

  test('TRUE: both present', () => {
    expect(evalIf(ifs.hasContact, { contact_email: 'a@b.com', contact_phone: '+1111' })).toBe(true);
  });

  test('FALSE: both empty strings', () => {
    expect(evalIf(ifs.hasContact, { contact_email: '', contact_phone: '' })).toBe(false);
  });

  test('FALSE: both missing', () => {
    expect(evalIf(ifs.hasContact, {})).toBe(false);
  });
});

// ─── Order Delivered? — IF node ───────────────────────────────────────────────

describe('Order Delivered? — IF node', () => {
  test('TRUE: shipday_status = "COMPLETED" from Normalise Orders node', () => {
    const nodeData = { 'Normalise Orders': [{ shipday_status: 'COMPLETED' }] };
    expect(evalIf(ifs.orderDelivered, {}, nodeData)).toBe(true);
  });

  test('FALSE: shipday_status = "ACCEPTED"', () => {
    const nodeData = { 'Normalise Orders': [{ shipday_status: 'ACCEPTED' }] };
    expect(evalIf(ifs.orderDelivered, {}, nodeData)).toBe(false);
  });

  test('FALSE: shipday_status = "FAILED"', () => {
    const nodeData = { 'Normalise Orders': [{ shipday_status: 'FAILED' }] };
    expect(evalIf(ifs.orderDelivered, {}, nodeData)).toBe(false);
  });

  test('FALSE: shipday_status missing', () => {
    const nodeData = { 'Normalise Orders': [{}] };
    expect(evalIf(ifs.orderDelivered, {}, nodeData)).toBe(false);
  });
});

// ─── Flow tests ───────────────────────────────────────────────────────────────

describe('Flow: completed delivery full path', () => {
  test('COMPLETED order → normalise → has contact → is delivered', async () => {
    const order = { orders: [{
      orderId: 'ORD-100', orderNumber: 'REF-100', orderStatus: 'COMPLETED',
      customer: { email: 'a@b.com', phone: '+1111', name: 'Alice' },
      assignedCarrier: null, estimatedDeliveryTime: null, actualDeliveryTime: null,
    }] };
    const items = await runCode(code.normalise, { inputItems: [order] });
    expect(items).toHaveLength(1);

    const item = items[0].json;
    expect(evalIf(ifs.hasContact, item)).toBe(true);

    // The 'Order Delivered?' IF reads from 'Normalise Orders' node via $()
    const nodeData = { 'Normalise Orders': [item] };
    expect(evalIf(ifs.orderDelivered, item, nodeData)).toBe(true);
  });
});

describe('Flow: accepted order → not completed branch', () => {
  test('ACCEPTED order → normalise → has contact → not delivered', async () => {
    const order = { orders: [{
      orderId: 'ORD-200', orderNumber: 'REF-200', orderStatus: 'ACCEPTED',
      customer: { email: 'b@b.com', phone: '+2222', name: 'Bob' },
      assignedCarrier: null, estimatedDeliveryTime: null, actualDeliveryTime: null,
    }] };
    const items = await runCode(code.normalise, { inputItems: [order] });
    const item = items[0].json;
    expect(evalIf(ifs.hasContact, item)).toBe(true);

    const nodeData = { 'Normalise Orders': [item] };
    expect(evalIf(ifs.orderDelivered, item, nodeData)).toBe(false);
  });
});

describe('Flow: order with no contact → filtered out', () => {
  test('order has no email and no phone → normalise returns nothing', async () => {
    const order = { orders: [{
      orderId: 'ORD-300', orderStatus: 'COMPLETED',
      customer: { email: '', phone: '', name: 'Ghost' },
    }] };
    const items = await runCode(code.normalise, { inputItems: [order] });
    expect(items).toHaveLength(0);
  });
});
