'use strict';
/**
 * Tests for [SMS] Inbound Collector — telnyx_inbound_collector.json
 *
 * Covers:
 *  Code: Build Time Window, Classify SMS + Signals, Classify Calls + Signals
 *  IF:   Has SMS?, Has Recording?
 */

const { runCode, evalIf, loadWorkflow, getCodeNode, getIfConditions } = require('../helpers/runtime');

let wf;
const code = {};
const ifs  = {};

beforeAll(() => {
  wf = loadWorkflow('telnyx_inbound_collector.json');
  code.buildWindow    = getCodeNode(wf, 'Build Time Window');
  code.classifySMS    = getCodeNode(wf, 'Classify SMS + Signals');
  code.classifyCalls  = getCodeNode(wf, 'Classify Calls + Signals');
  ifs.hasSMS          = getIfConditions(wf, 'Has SMS?');
  ifs.hasRecording    = getIfConditions(wf, 'Has Recording?');
});

// ─── Build Time Window ────────────────────────────────────────────────────────

describe('Build Time Window — Code node', () => {
  test('returns window_from and window_to', async () => {
    const result = await runCode(code.buildWindow);
    const out = result[0].json;
    expect(out).toHaveProperty('window_from');
    expect(out).toHaveProperty('window_to');
  });

  test('window_from is ~35 min before window_to', async () => {
    const result = await runCode(code.buildWindow);
    const fromMs = new Date(result[0].json.window_from).getTime();
    const toMs   = new Date(result[0].json.window_to).getTime();
    const diffMin = (toMs - fromMs) / 60000;
    expect(Math.abs(diffMin - 35)).toBeLessThan(1);
  });

  test('both values are ISO date strings', async () => {
    const result = await runCode(code.buildWindow);
    const out = result[0].json;
    expect(new Date(out.window_from).getTime()).not.toBeNaN();
    expect(new Date(out.window_to).getTime()).not.toBeNaN();
  });
});

// ─── Classify SMS + Signals ───────────────────────────────────────────────────

describe('Classify SMS + Signals — Code node', () => {
  const makeMDR = (body, from = '+14165550001', to = '+18444322224') => ({
    data: [{ id: 'msg-1', from_number: from, to_number: to, message_body: body, status: 'received' }]
  });

  test('ORDER_KEYWORDS → order_intent_sms', async () => {
    const cases = ['I want to order food', 'can I order now?', 'how do I order?', 'place an order'];
    for (const body of cases) {
      const result = await runCode(code.classifySMS, { inputItems: [makeMDR(body)] });
      expect(result[0].json.event_type).toBe('order_intent_sms');
    }
  });

  test('REORDER_KEYWORDS → order_intent_sms (same branch as order)', async () => {
    const cases = ['same as last time', 'I want to reorder', 'another one please', 'refill my order'];
    for (const body of cases) {
      const result = await runCode(code.classifySMS, { inputItems: [makeMDR(body)] });
      expect(result[0].json.event_type).toBe('order_intent_sms');
    }
  });

  test('DELIVERY_KEYWORDS → delivery_inquiry_sms', async () => {
    const cases = ['Is my delivery coming?', 'tracking number?', 'when will package arrive?', 'has it been picked up?'];
    for (const body of cases) {
      const result = await runCode(code.classifySMS, { inputItems: [makeMDR(body)] });
      expect(result[0].json.event_type).toBe('delivery_inquiry_sms');
    }
  });

  test('PRICE_KEYWORDS → price_inquiry_sms', async () => {
    const cases = ['What is the price?', 'any discount today?', 'how much does it cost?', 'got a promo?'];
    for (const body of cases) {
      const result = await runCode(code.classifySMS, { inputItems: [makeMDR(body)] });
      expect(result[0].json.event_type).toBe('price_inquiry_sms');
    }
  });

  test('generic message → sms_received', async () => {
    const result = await runCode(code.classifySMS, { inputItems: [makeMDR('Thanks!')] });
    expect(result[0].json.event_type).toBe('sms_received');
  });

  test('order keyword takes priority over delivery keyword', async () => {
    // 'I want to order and track my package' — has both order + delivery signals
    const result = await runCode(code.classifySMS, { inputItems: [makeMDR('I want to order and track my package')] });
    expect(result[0].json.event_type).toBe('order_intent_sms');
  });

  test('filters out messages with no from_number', async () => {
    const mdr = { data: [{ id: 'msg-2', from_number: '', to_number: '+18444322224', message_body: 'Hello' }] };
    const result = await runCode(code.classifySMS, { inputItems: [mdr] });
    expect(result).toHaveLength(0);
  });

  test('sets is_order_signal = true for order keywords', async () => {
    const result = await runCode(code.classifySMS, { inputItems: [makeMDR('I want to order')] });
    expect(result[0].json.is_order_signal).toBe(true);
  });

  test('sets is_order_signal = false for non-order messages', async () => {
    const result = await runCode(code.classifySMS, { inputItems: [makeMDR('Thanks!')] });
    expect(result[0].json.is_order_signal).toBe(false);
  });

  test('includes from_number, to_number, body in output', async () => {
    const result = await runCode(code.classifySMS, { inputItems: [makeMDR('Test message')] });
    const out = result[0].json;
    expect(out.from_number).toBe('+14165550001');
    expect(out.to_number).toBe('+18444322224');
    expect(out.body).toBe('Test message');
  });

  test('handles alternative MDR format (from.phone_number)', async () => {
    const mdr = {
      data: [{
        id: 'msg-3',
        from: { phone_number: '+14165550002' },
        to: [{ phone_number: '+18444322224' }],
        message_body: 'Hi',
        status: 'received',
      }]
    };
    const result = await runCode(code.classifySMS, { inputItems: [mdr] });
    expect(result).toHaveLength(1);
    expect(result[0].json.from_number).toBe('+14165550002');
  });

  test('handles empty data array', async () => {
    const result = await runCode(code.classifySMS, { inputItems: [{ data: [] }] });
    expect(result).toHaveLength(0);
  });

  test('handles missing data field', async () => {
    const result = await runCode(code.classifySMS, { inputItems: [{}] });
    expect(result).toHaveLength(0);
  });

  test('metadata includes signals object', async () => {
    const result = await runCode(code.classifySMS, { inputItems: [makeMDR('I want to order')] });
    expect(result[0].json.metadata.signals).toHaveProperty('isOrderSignal');
    expect(result[0].json.metadata.signals).toHaveProperty('isDeliverySignal');
  });
});

// ─── Classify Calls + Signals ─────────────────────────────────────────────────

describe('Classify Calls + Signals — Code node', () => {
  const makeRecording = (transcript, from = '+14165550001', to = '+18444322224') => ({
    data: [{
      id: 'rec-1', from, to,
      direction: 'inbound',
      transcription: { transcript },
      download_url: 'https://example.com/recording.mp3',
      duration_secs: 120,
      start_time: '2024-01-01T12:00:00Z',
      end_time: '2024-01-01T12:02:00Z',
    }]
  });

  test('REORDER_KEYWORDS → reorder_intent_call', async () => {
    const cases = [
      'I want to order again same as last time',
      'please reorder my usual',
      'repeat my previous order',
      'refill my subscription',
    ];
    for (const transcript of cases) {
      const result = await runCode(code.classifyCalls, { inputItems: [makeRecording(transcript)] });
      expect(result[0].json.event_type).toBe('reorder_intent_call');
    }
  });

  test('COMPLAINT_WORDS → complaint_call', async () => {
    const cases = [
      'My food was cold when it arrived',
      'I want a refund, the order was wrong',
      'Very unhappy with the delivery, very late',
      'Order was missing items',
    ];
    for (const transcript of cases) {
      const result = await runCode(code.classifyCalls, { inputItems: [makeRecording(transcript)] });
      expect(result[0].json.event_type).toBe('complaint_call');
    }
  });

  test('generic call → call_completed', async () => {
    const result = await runCode(code.classifyCalls, { inputItems: [makeRecording('Thank you for the food')] });
    expect(result[0].json.event_type).toBe('call_completed');
  });

  test('reorder takes priority over complaint in signal detection', async () => {
    // 'another order' (reorder) + 'late' (complaint) — reorder wins as it's checked first
    const result = await runCode(code.classifyCalls, { inputItems: [makeRecording('another order was late')] });
    expect(result[0].json.event_type).toBe('reorder_intent_call');
  });

  test('filters out recordings with no from_number', async () => {
    const rec = {
      data: [{
        id: 'rec-2', from: '', to: '+18444322224',
        transcription: { transcript: 'hello' }, duration_secs: 60,
      }]
    };
    const result = await runCode(code.classifyCalls, { inputItems: [rec] });
    expect(result).toHaveLength(0);
  });

  test('has_order_signal = true for reorder keywords', async () => {
    const result = await runCode(code.classifyCalls, { inputItems: [makeRecording('please reorder my order')] });
    expect(result[0].json.has_order_signal).toBe(true);
  });

  test('has_order_signal = false for generic call', async () => {
    const result = await runCode(code.classifyCalls, { inputItems: [makeRecording('Great service')] });
    expect(result[0].json.has_order_signal).toBe(false);
  });

  test('includes recording_url in output', async () => {
    const result = await runCode(code.classifyCalls, { inputItems: [makeRecording('Hello')] });
    expect(result[0].json.recording_url).toBe('https://example.com/recording.mp3');
  });

  test('handles missing transcript gracefully', async () => {
    const rec = {
      data: [{
        id: 'rec-3', from: '+14165550001', to: '+18444322224',
        transcription: null, duration_secs: 60,
      }]
    };
    const result = await runCode(code.classifyCalls, { inputItems: [rec] });
    // Should not throw; event_type = call_completed since no keywords matched
    expect(result).toHaveLength(1);
    expect(result[0].json.event_type).toBe('call_completed');
  });

  test('handles empty data array', async () => {
    const result = await runCode(code.classifyCalls, { inputItems: [{ data: [] }] });
    expect(result).toHaveLength(0);
  });

  test('duration_sec is rounded integer', async () => {
    const result = await runCode(code.classifyCalls, { inputItems: [makeRecording('Hi')] });
    expect(result[0].json.duration_sec).toBe(120);
    expect(Number.isInteger(result[0].json.duration_sec)).toBe(true);
  });
});

// ─── Has SMS? and Has Recording? — IF nodes ───────────────────────────────────

describe('Has SMS? — IF node', () => {
  test('TRUE: from_number is non-empty', () => {
    expect(evalIf(ifs.hasSMS, { from_number: '+14165550001' })).toBe(true);
  });
  test('FALSE: from_number is empty string', () => {
    expect(evalIf(ifs.hasSMS, { from_number: '' })).toBe(false);
  });
  test('FALSE: from_number is missing', () => {
    expect(evalIf(ifs.hasSMS, {})).toBe(false);
  });
});

describe('Has Recording? — IF node', () => {
  test('TRUE: from_number is non-empty', () => {
    expect(evalIf(ifs.hasRecording, { from_number: '+14165550001' })).toBe(true);
  });
  test('FALSE: from_number is empty string', () => {
    expect(evalIf(ifs.hasRecording, { from_number: '' })).toBe(false);
  });
  test('FALSE: from_number is missing', () => {
    expect(evalIf(ifs.hasRecording, {})).toBe(false);
  });
});

// ─── Flow tests ───────────────────────────────────────────────────────────────

describe('Flow: order intent SMS → classify → store + ingest', () => {
  test('order keyword → order_intent_sms + is_order_signal true', async () => {
    const mdr = { data: [{ id: 'msg-10', from_number: '+14165550001', to_number: '+18444322224', message_body: 'I want to order some food', status: 'received' }] };
    const result = await runCode(code.classifySMS, { inputItems: [mdr] });
    expect(result[0].json.event_type).toBe('order_intent_sms');
    expect(result[0].json.is_order_signal).toBe(true);
    // IF guard passes because from_number is set
    expect(evalIf(ifs.hasSMS, result[0].json)).toBe(true);
  });
});

describe('Flow: filtered SMS (no from_number) → IF blocks', () => {
  test('empty from_number → classify returns nothing → IF would be false anyway', async () => {
    const mdr = { data: [{ id: 'msg-11', from_number: '', to_number: '+18444322224', message_body: 'order' }] };
    const result = await runCode(code.classifySMS, { inputItems: [mdr] });
    expect(result).toHaveLength(0);
  });
});

describe('Flow: reorder call → complaint vs reorder priority', () => {
  test('reorder signal wins over complaint signal', async () => {
    const rec = { data: [{ id: 'rec-20', from: '+14165550001', to: '+18444322224', transcription: { transcript: 'order again but it was a bit late' }, duration_secs: 90 }] };
    const result = await runCode(code.classifyCalls, { inputItems: [rec] });
    expect(result[0].json.event_type).toBe('reorder_intent_call');
    expect(evalIf(ifs.hasRecording, result[0].json)).toBe(true);
  });
});
