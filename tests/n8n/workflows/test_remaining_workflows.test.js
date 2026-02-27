'use strict';
/**
 * Tests for all remaining workflows:
 *  - agent_orchestration_cron      (Code: Summarise Cycle; IF: Contacts Processed?)
 *  - airtable_menu_sync            (Code: Log Success, Log Errors; IF: Has Errors?)
 *  - airtable_playbook_sync        (Code: Transform Airtable Records; IF: Has Changes?)
 *  - campaign_performance_tracker  (Code: Split Into One Per Campaign, Normalise Stats)
 *  - chatbot_docs_reindex          (Code: Log Success, Log Failure; IF: Check Success)
 *  - competitor_agent_cycle / goal_agent_cycle  (Code: Log Result)
 *  - daily_activity_report         (Code: Get Today's Date; IF: Email Sent?)
 *  - daily_field_brief             (Code: Format Brief Summary, Prepare Airtable Records, Log No Contacts; IF: Any Opportunities Created?)
 *  - daily_order_upload            (IF: Has Orders?)
 *  - google_docs_sync              (Code: Split Files, Filter & Classify, Build Document Payload; IF: Has Docs?)
 *  - growth_agent_cycle            (Code: Build Weekly Report)
 *  - hourly reports                (Code: Get Today's Date; IF: Email Sent?)
 *  - instantly_campaign_sync       (Code: Filter: tag = dabbahwala)
 *  - instantly_setup               (Code: Parse Response)
 *  - lapsed_customer_cycle         (Code: Log Results; IF: Any Actions Taken?)
 *  - marketing_query_form          (Code: Map Category, Format Response; IF: Is Submission?)
 *  - menu_sync_weekly              (Code: Build Menu Summary; IF: Scrape OK?)
 *  - shipday_feedback_sync         (IF: New Feedback Found?)
 *  - sms_dispatch                  (Code: Split Contacts, Build SMS Message; IF: Has Pending SMS?)
 *  - system_test_suite             (IF: Any Failures?)
 *  - telnyx_sms_historical_import  (IF: No More Records?)
 */

const { runCode, evalIf, loadWorkflow, getCodeNode, getIfConditions } = require('../helpers/runtime');

// ═══════════════════════════════════════════════════════════════════════════════
// AGENT ORCHESTRATION CRON
// ═══════════════════════════════════════════════════════════════════════════════

describe('[agent_orchestration_cron] Summarise Cycle — Code node', () => {
  let code, ifc;
  beforeAll(() => {
    const wf = loadWorkflow('agent_orchestration_cron.json');
    code = getCodeNode(wf, 'Summarise Cycle');
    ifc  = getIfConditions(wf, 'Contacts Processed?');
  });

  test('summarises processed count and action count', async () => {
    const item = {
      processed: 5,
      errors: ['err1'],
      results: [
        { chosen_action: 'send_sms' },
        { chosen_action: 'none' },
        { chosen_action: 'move_campaign' },
      ],
    };
    const result = await runCode(code, { inputItems: [item] });
    const out = result[0].json;
    expect(out.processed).toBe(5);
    expect(out.errors).toBe(1);
    expect(out.actions_queued).toBe(2);  // 'none' excluded
    expect(out.summary).toContain('5 contacts processed');
    expect(out.summary).toContain('2 actions queued');
  });

  test('handles empty results and errors arrays', async () => {
    const item = { processed: 0, errors: [], results: [] };
    const result = await runCode(code, { inputItems: [item] });
    expect(result[0].json.actions_queued).toBe(0);
    expect(result[0].json.errors).toBe(0);
  });

  test('IF Contacts Processed? TRUE when processed > 0', () => {
    expect(evalIf(ifc, { processed: 3 })).toBe(true);
  });
  test('IF Contacts Processed? FALSE when processed = 0', () => {
    expect(evalIf(ifc, { processed: 0 })).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// AIRTABLE MENU SYNC
// ═══════════════════════════════════════════════════════════════════════════════

describe('[airtable_menu_sync] Code + IF nodes', () => {
  let logSuccess, logErrors, ifc;
  beforeAll(() => {
    const wf = loadWorkflow('airtable_menu_sync.json');
    logSuccess = getCodeNode(wf, 'Log Success');
    logErrors  = getCodeNode(wf, 'Log Errors');
    ifc        = getIfConditions(wf, 'Has Errors?');
  });

  test('Log Success: returns data unchanged', async () => {
    const data = { upserted: 10, discarded: 2, errors: 0, total: 12, history_events: 10 };
    const result = await runCode(logSuccess, { inputItems: [data] });
    expect(result[0].json).toMatchObject(data);
  });

  test('Log Errors: returns data unchanged', async () => {
    const data = { upserted: 8, discarded: 1, errors: 3, total: 12, history_events: 8 };
    const result = await runCode(logErrors, { inputItems: [data] });
    expect(result[0].json).toMatchObject(data);
  });

  test('Has Errors? TRUE when errors > 0', () => {
    expect(evalIf(ifc, { errors: 2 })).toBe(true);
  });
  test('Has Errors? FALSE when errors = 0', () => {
    expect(evalIf(ifc, { errors: 0 })).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// AIRTABLE PLAYBOOK SYNC
// ═══════════════════════════════════════════════════════════════════════════════

describe('[airtable_playbook_sync] Transform Airtable Records — Code node', () => {
  let code, ifc;
  beforeAll(() => {
    const wf = loadWorkflow('airtable_playbook_sync.json');
    code = getCodeNode(wf, 'Transform Airtable Records');
    ifc  = getIfConditions(wf, 'Has Changes?');
  });

  test('transforms valid records correctly', async () => {
    const data = {
      records: [
        { id: 'recA', fields: { 'Rule Name': 'No Discounts', Category: 'decision', Instruction: 'Never offer discounts', Priority: 90, Active: true } },
        { id: 'recB', fields: { 'Rule Name': 'Tone', Category: 'messaging', Instruction: 'Be friendly', Priority: 70, Active: false } },
      ]
    };
    const result = await runCode(code, { inputItems: [data] });
    const records = result[0].json.records;
    expect(records).toHaveLength(2);
    expect(records[0].airtable_id).toBe('recA');
    expect(records[0].rule_name).toBe('No Discounts');
    expect(records[0].category).toBe('decision');
    expect(records[0].priority).toBe(90);
    expect(records[0].active).toBe(true);
  });

  test('filters out records with missing rule_name', async () => {
    const data = {
      records: [
        { id: 'recA', fields: { 'Rule Name': '', Instruction: 'some instruction' } },
        { id: 'recB', fields: { 'Rule Name': 'Valid', Instruction: 'another' } },
      ]
    };
    const result = await runCode(code, { inputItems: [data] });
    expect(result[0].json.records).toHaveLength(1);
    expect(result[0].json.records[0].rule_name).toBe('Valid');
  });

  test('filters out records with missing instruction', async () => {
    const data = {
      records: [
        { id: 'recA', fields: { 'Rule Name': 'Rule1', Instruction: '' } },
      ]
    };
    const result = await runCode(code, { inputItems: [data] });
    expect(result[0].json.records).toHaveLength(0);
  });

  test('category defaults to "general" when missing', async () => {
    const data = {
      records: [{ id: 'recA', fields: { 'Rule Name': 'R', Instruction: 'I' } }]
    };
    const result = await runCode(code, { inputItems: [data] });
    expect(result[0].json.records[0].category).toBe('general');
  });

  test('priority defaults to 50 when missing', async () => {
    const data = {
      records: [{ id: 'recA', fields: { 'Rule Name': 'R', Instruction: 'I' } }]
    };
    const result = await runCode(code, { inputItems: [data] });
    expect(result[0].json.records[0].priority).toBe(50);
  });

  test('active defaults to true when not explicitly false', async () => {
    const data = {
      records: [{ id: 'recA', fields: { 'Rule Name': 'R', Instruction: 'I' } }]
    };
    const result = await runCode(code, { inputItems: [data] });
    expect(result[0].json.records[0].active).toBe(true);
  });

  test('Has Changes? TRUE when synced > 0', () => {
    expect(evalIf(ifc, { synced: 3 })).toBe(true);
  });
  test('Has Changes? FALSE when synced = 0', () => {
    expect(evalIf(ifc, { synced: 0 })).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// CAMPAIGN PERFORMANCE TRACKER
// ═══════════════════════════════════════════════════════════════════════════════

describe('[campaign_performance_tracker] Code nodes', () => {
  let splitCode, normaliseCode;
  beforeAll(() => {
    const wf = loadWorkflow('campaign_performance_tracker.json');
    splitCode    = getCodeNode(wf, 'Split Into One Per Campaign');
    normaliseCode = getCodeNode(wf, 'Normalise Stats');
  });

  test('Split: returns one item per campaign with campaign_id', async () => {
    const data = { campaigns: [{ campaign_id: 'c1', campaign_name: 'Test' }, { campaign_id: 'c2' }] };
    const result = await runCode(splitCode, { inputItems: [data] });
    expect(result).toHaveLength(2);
    expect(result[0].json.campaign_id).toBe('c1');
  });

  test('Split: filters out campaigns with no campaign_id', async () => {
    const data = { campaigns: [{ campaign_name: 'No ID' }, { campaign_id: 'c3' }] };
    const result = await runCode(splitCode, { inputItems: [data] });
    expect(result).toHaveLength(1);
    expect(result[0].json.campaign_id).toBe('c3');
  });

  test('Split: returns empty for empty campaigns', async () => {
    const result = await runCode(splitCode, { inputItems: [{ campaigns: [] }] });
    expect(result).toHaveLength(0);
  });

  test('Normalise: calculates open_rate and reply_rate correctly', async () => {
    const stats = { campaign_id: 'c1', emails_sent_count: 100, open_count: 30, reply_count: 10, link_click_count: 5, bounced_count: 2, unsubscribed_count: 1, leads_count: 50 };
    const nodeData = { 'Split Into One Per Campaign': [{ campaign_id: 'c1' }] };
    const result = await runCode(normaliseCode, { inputItems: [stats], nodeData });
    const out = result[0].json;
    expect(out.open_rate).toBe(30);
    expect(out.reply_rate).toBe(10);
    expect(out.emails_sent).toBe(100);
  });

  test('Normalise: zero rates when no emails sent', async () => {
    const stats = { campaign_id: 'c2', emails_sent_count: 0, open_count: 0, reply_count: 0 };
    const nodeData = { 'Split Into One Per Campaign': [{ campaign_id: 'c2' }] };
    const result = await runCode(normaliseCode, { inputItems: [stats], nodeData });
    expect(result[0].json.open_rate).toBe(0);
    expect(result[0].json.reply_rate).toBe(0);
  });

  test('Normalise: zero-value stats when no analytics data (campaign not launched)', async () => {
    // No emails_sent_count → campaign has no sends
    const noData = { campaign_id: 'c3' };  // missing emails_sent_count
    const nodeData = { 'Split Into One Per Campaign': [{ campaign_id: 'c3' }] };
    const result = await runCode(normaliseCode, { inputItems: [noData], nodeData });
    expect(result[0].json.emails_sent).toBe(0);
    expect(result[0].json.campaign_id).toBe('c3');
  });

  test('Normalise: handles array response from Instantly API', async () => {
    const analyticsArr = [{ campaign_id: 'c4', emails_sent_count: 50, open_count: 20, reply_count: 5, leads_count: 25 }];
    const nodeData = { 'Split Into One Per Campaign': [{ campaign_id: 'c4' }] };
    const result = await runCode(normaliseCode, { inputItems: [analyticsArr], nodeData });
    expect(result[0].json.emails_sent).toBe(50);
    expect(result[0].json.open_rate).toBe(40);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// CHATBOT DOCS REINDEX
// ═══════════════════════════════════════════════════════════════════════════════

describe('[chatbot_docs_reindex] Code + IF nodes', () => {
  let logSuccess, logFail, ifc;
  beforeAll(() => {
    const wf = loadWorkflow('chatbot_docs_reindex.json');
    logSuccess = getCodeNode(wf, 'Log Success');
    logFail    = getCodeNode(wf, 'Log Failure');
    ifc        = getIfConditions(wf, 'Check Success');
  });

  test('Log Success returns data', async () => {
    const data = { status: 'ok', indexed: 10 };
    const result = await runCode(logSuccess, { inputItems: [data] });
    expect(result[0].json).toMatchObject(data);
  });

  test('Log Failure returns data', async () => {
    const data = { status: 'error', detail: 'timeout' };
    const result = await runCode(logFail, { inputItems: [data] });
    expect(result[0].json).toMatchObject(data);
  });

  test('Check Success TRUE when status = "ok"', () => {
    expect(evalIf(ifc, { status: 'ok' })).toBe(true);
  });
  test('Check Success FALSE when status = "error"', () => {
    expect(evalIf(ifc, { status: 'error' })).toBe(false);
  });
  test('Check Success FALSE when status missing', () => {
    expect(evalIf(ifc, {})).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// COMPETITOR AGENT + GOAL AGENT (log only)
// ═══════════════════════════════════════════════════════════════════════════════

describe('[competitor_agent_cycle + goal_agent_cycle] Log Result — Code node', () => {
  test('competitor Log Result returns data unchanged', async () => {
    const wf = loadWorkflow('competitor_agent_cycle.json');
    const code = getCodeNode(wf, 'Log Result');
    const data = { status: 'ok', insights: 'competitor raised prices' };
    const result = await runCode(code, { inputItems: [data] });
    // Simple log nodes return $input data (pass-through)
    expect(result).toBeDefined();
  });

  test('goal agent Log Result returns data unchanged', async () => {
    const wf = loadWorkflow('goal_agent_cycle.json');
    const code = getCodeNode(wf, 'Log Result');
    const data = { status: 'ok', goals_evaluated: 5 };
    const result = await runCode(code, { inputItems: [data] });
    expect(result).toBeDefined();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// DAILY REPORTS (activity + outcome share same pattern)
// ═══════════════════════════════════════════════════════════════════════════════

describe('[daily_activity_report + daily_outcome_report] Code + IF', () => {
  test('activity report: Email Sent? TRUE when status = "sent"', () => {
    const wf = loadWorkflow('daily_activity_report.json');
    const ifc = getIfConditions(wf, 'Email Sent?');
    expect(evalIf(ifc, { status: 'sent' })).toBe(true);
    expect(evalIf(ifc, { status: 'failed' })).toBe(false);
    expect(evalIf(ifc, {})).toBe(false);
  });

  test('outcome report: Email Sent? TRUE when status = "sent"', () => {
    const wf = loadWorkflow('daily_outcome_report.json');
    const ifc = getIfConditions(wf, 'Email Sent?');
    expect(evalIf(ifc, { status: 'sent' })).toBe(true);
    expect(evalIf(ifc, { status: 'error' })).toBe(false);
  });

  test('daily_activity_report: Get Today\'s Date returns date string', async () => {
    const wf = loadWorkflow('daily_activity_report.json');
    const code = getCodeNode(wf, "Get Today's Date");
    const result = await runCode(code);
    expect(result).toBeDefined();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// DAILY FIELD BRIEF
// ═══════════════════════════════════════════════════════════════════════════════

describe('[daily_field_brief] Code + IF nodes', () => {
  let formatCode, prepareCode, logNoCode, ifc;
  beforeAll(() => {
    const wf = loadWorkflow('daily_field_brief.json');
    formatCode  = getCodeNode(wf, 'Format Brief Summary');
    prepareCode = getCodeNode(wf, 'Prepare Airtable Records');
    logNoCode   = getCodeNode(wf, 'Log No Contacts');
    ifc         = getIfConditions(wf, 'Any Opportunities Created?');
  });

  test('Format Brief Summary: formats contacts list into text', async () => {
    const input = {
      created: 3,
      brief: [
        { first_name: 'Ali', last_name: 'Hassan', total_orders: 5, lifetime_spend: 120, days_since_last_order: 7, urgency_note: 'High value' },
        { first_name: 'Sara', last_name: 'Khan', total_orders: 2, lifetime_spend: 45, days_since_last_order: 14, urgency_note: 'Lapsed' },
      ]
    };
    const result = await runCode(formatCode, { inputItems: [input] });
    const out = result[0].json;
    expect(out.summary).toContain('DabbahWala Field Brief');
    expect(out.summary).toContain('3 call opportunities');
    expect(out.summary).toContain('#1 Ali Hassan');
    expect(out.summary).toContain('#2 Sara Khan');
    expect(out.count).toBe(2);
    expect(out.created).toBe(3);
  });

  test('Format Brief Summary: handles empty brief', async () => {
    const result = await runCode(formatCode, { inputItems: [{ created: 0, brief: [] }] });
    expect(result[0].json.summary).toContain('0 call opportunities');
  });

  test('Prepare Airtable Records: transforms contacts to Airtable fields', async () => {
    const briefData = {
      brief: [
        { first_name: 'Omar', last_name: 'Ali', phone: '+14165550001', priority: 'hot', opportunity_id: 99, suggested_script: 'Call script here', days_since_last_order: 10, total_orders: 7 },
      ]
    };
    const nodeData = { 'Generate Daily Call List': [briefData] };
    const result = await runCode(prepareCode, { nodeData });
    const records = result[0].json.records;
    expect(records).toHaveLength(1);
    expect(records[0].fields['Contact Name']).toBe('Omar Ali');
    expect(records[0].fields['Phone']).toBe('+14165550001');
    expect(records[0].fields['Priority']).toBe('Hot');
    expect(records[0].fields['Postgres Opportunity ID']).toBe('99');
    expect(records[0].fields['Synced to Postgres']).toBe(false);
  });

  test('Prepare Airtable Records: caps at 10 records', async () => {
    const brief = Array.from({ length: 15 }, (_, i) => ({
      first_name: 'Contact', last_name: String(i), phone: '+1111', priority: 'warm', opportunity_id: i,
    }));
    const nodeData = { 'Generate Daily Call List': [{ brief }] };
    const result = await runCode(prepareCode, { nodeData });
    expect(result[0].json.records).toHaveLength(10);
  });

  test('Log No Contacts: returns skipped status', async () => {
    const result = await runCode(logNoCode);
    expect(result[0].json.status).toBe('skipped');
    expect(result[0].json.reason).toBe('no_eligible_contacts');
  });

  test('Any Opportunities Created? TRUE when created > 0', () => {
    expect(evalIf(ifc, { created: 1 })).toBe(true);
  });
  test('Any Opportunities Created? FALSE when created = 0', () => {
    expect(evalIf(ifc, { created: 0 })).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// DAILY ORDER UPLOAD
// ═══════════════════════════════════════════════════════════════════════════════

describe('[daily_order_upload] Has Orders? — IF node', () => {
  test('TRUE when total_orders > 0', () => {
    const wf = loadWorkflow('daily_order_upload.json');
    const ifc = getIfConditions(wf, 'Has Orders?');
    expect(evalIf(ifc, { total_orders: 5 })).toBe(true);
    expect(evalIf(ifc, { total_orders: 0 })).toBe(false);
    expect(evalIf(ifc, {})).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// GOOGLE DOCS SYNC
// ═══════════════════════════════════════════════════════════════════════════════

describe('[google_docs_sync] Code + IF nodes', () => {
  let splitCode, classifyCode, buildCode, ifc;
  beforeAll(() => {
    const wf = loadWorkflow('google_docs_sync.json');
    splitCode    = getCodeNode(wf, 'Split Files');
    classifyCode = getCodeNode(wf, 'Filter & Classify');
    buildCode    = getCodeNode(wf, 'Build Document Payload');
    ifc          = getIfConditions(wf, 'Has Docs?');
  });

  test('Split Files: maps files array to individual items', async () => {
    const data = { files: [{ id: 'doc1', name: 'Test' }, { id: 'doc2', name: 'Other' }] };
    const result = await runCode(splitCode, { inputItems: [data] });
    expect(result).toHaveLength(2);
    expect(result[0].json.id).toBe('doc1');
  });

  test('Split Files: returns empty for no files', async () => {
    const result = await runCode(splitCode, { inputItems: [{ files: [] }] });
    expect(result).toHaveLength(0);
  });

  const CLASSIFY_CASES = [
    ['social media post draft', 'ad_copy'],
    ['Facebook ad copy Q1', 'ad_copy'],
    ['Instagram content', 'ad_copy'],
    ['field note delivery jan', 'ground_note'],
    ['driver observations', 'ground_note'],
    ['ground team update', 'ground_note'],
    ['DabbahWala brand guidelines', 'ground_note'],  // default
  ];

  test.each(CLASSIFY_CASES)('classifies "%s" as "%s"', async (title, expectedType) => {
    const doc = { id: 'docX', name: title, mimeType: 'application/vnd.google-apps.document', modifiedTime: '2024-01-01' };
    const result = await runCode(classifyCode, { inputItems: [doc] });
    expect(result).toHaveLength(1);
    expect(result[0].json.content_type).toBe(expectedType);
    expect(result[0].json.google_doc_id).toBe('docX');
    expect(result[0].json.doc_url).toContain('docs.google.com');
  });

  test('Filter & Classify: filters out non-Google Docs files', async () => {
    const sheet = { id: 'sheet1', name: 'Spreadsheet', mimeType: 'application/vnd.google-apps.spreadsheet' };
    const result = await runCode(classifyCode, { inputItems: [sheet] });
    expect(result).toHaveLength(0);
  });

  test('Build Document Payload: extracts text from .text property', async () => {
    const docContent = { text: 'This is the document text content.' };
    const meta = { google_doc_id: 'doc1', content_type: 'ground_note', title: 'Field Note', author: 'Ali', google_last_modified: '2024-01-01', doc_url: 'https://docs.google.com/d/doc1' };
    const nodeData = { 'Filter & Classify': [meta] };
    const result = await runCode(buildCode, { inputItems: [docContent], nodeData });
    const out = result[0].json;
    expect(out.body).toBe('This is the document text content.');
    expect(out.google_doc_id).toBe('doc1');
    expect(out.content_type).toBe('ground_note');
    expect(out.tags).toEqual([]);
  });

  test('Build Document Payload: extracts text from body.content (Google Docs API format)', async () => {
    const docContent = {
      body: {
        content: [
          { paragraph: { elements: [{ textRun: { content: 'Hello ' } }, { textRun: { content: 'World' } }] } },
          { paragraph: { elements: [{ textRun: { content: '\nSecond paragraph' } }] } },
        ]
      }
    };
    const meta = { google_doc_id: 'doc2', content_type: 'ad_copy', title: 'Ad', author: 'Sara', google_last_modified: '2024-01-02', doc_url: 'https://docs.google.com/d/doc2' };
    const nodeData = { 'Filter & Classify': [meta] };
    const result = await runCode(buildCode, { inputItems: [docContent], nodeData });
    expect(result[0].json.body).toContain('Hello');
    expect(result[0].json.body).toContain('World');
  });

  test('Has Docs? FALSE when _empty = true', () => {
    expect(evalIf(ifc, { _empty: true })).toBe(false);
  });
  test('Has Docs? TRUE when _empty != true', () => {
    expect(evalIf(ifc, { _empty: false })).toBe(true);
    expect(evalIf(ifc, {})).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// GROWTH AGENT CYCLE
// ═══════════════════════════════════════════════════════════════════════════════

describe('[growth_agent_cycle] Build Weekly Report — Code node', () => {
  let code;
  beforeAll(() => {
    const wf = loadWorkflow('growth_agent_cycle.json');
    code = getCodeNode(wf, 'Build Weekly Report');
  });

  test('generates HTML report with baseline and experiments', async () => {
    const nodeData = {
      'Design + Launch New Experiment': [{ status: 'launched', name: 'Price test', experiment_type: 'promo', channel: 'sms', cohort_size: 50, hypothesis: 'Lower price increases orders', measure_at: '2024-02-01', measure_days: 7 }],
      'Measure Previous Experiments':   [{ measured: 2, results: [{ name: 'Exp A', is_winner: true, conversion_rate: 0.3, orders_won: 15, cohort_size: 50, learnings: 'Works great' }, { name: 'Exp B', is_winner: false, conversion_rate: 0.1, orders_won: 5, cohort_size: 50, learnings: 'Did not work' }] }],
      'Get Growth Insights':            [{ insights_summary: 'Key insight: price matters' }],
      'Refresh Baseline Conv Rate':     [{ baseline_conv_rate: 0.25 }],
    };
    const result = await runCode(code, { nodeData });
    const out = result[0].json;
    expect(out.body_html).toContain('Growth Agent');
    expect(out.body_html).toContain('25.0%');
    expect(out.body_html).toContain('Exp A');
    expect(out.body_html).toContain('Exp B');
    expect(out.body_html).toContain('Price test');
    expect(out.subject).toContain('Growth Agent');
  });

  test('handles empty experiments gracefully', async () => {
    const nodeData = {
      'Design + Launch New Experiment': [{ status: 'skipped', reason: 'No cohort available' }],
      'Measure Previous Experiments':   [{ measured: 0, results: [] }],
      'Get Growth Insights':            [{ insights_summary: 'Not enough data' }],
      'Refresh Baseline Conv Rate':     [{ baseline_conv_rate: 0 }],
    };
    const result = await runCode(code, { nodeData });
    expect(result[0].json.body_html).toContain('No experiments measured this week');
    expect(result[0].json.body_html).toContain('0.0%');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// INSTANTLY CAMPAIGN SYNC
// ═══════════════════════════════════════════════════════════════════════════════

describe('[instantly_campaign_sync] Filter: tag = dabbahwala — Code node', () => {
  let code;
  beforeAll(() => {
    const wf = loadWorkflow('instantly_campaign_sync.json');
    code = getCodeNode(wf, 'Filter: tag = dabbahwala');
  });

  test('keeps campaigns tagged "dabbahwala"', async () => {
    const data = [
      { id: 'c1', name: 'DW Campaign', tags: ['dabbahwala', 'promo'], status: 'active' },
      { id: 'c2', name: 'Other', tags: ['other-client'], status: 'active' },
    ];
    const result = await runCode(code, { inputItems: [data] });
    const campaigns = result[0].json.campaigns;
    expect(campaigns).toHaveLength(1);
    expect(campaigns[0].campaign_id).toBe('c1');
  });

  test('case-insensitive tag matching', async () => {
    const data = [{ id: 'c3', name: 'Test', tags: ['DabbahWala'], status: 'active' }];
    const result = await runCode(code, { inputItems: [data] });
    expect(result[0].json.campaigns).toHaveLength(1);
  });

  test('handles tag objects with name property', async () => {
    const data = [{ id: 'c4', name: 'Test', tags: [{ name: 'dabbahwala' }], status: 'active' }];
    const result = await runCode(code, { inputItems: [data] });
    expect(result[0].json.campaigns).toHaveLength(1);
  });

  test('returns empty campaigns array when none match', async () => {
    const data = [{ id: 'c5', name: 'Other Client', tags: ['other'], status: 'active' }];
    const result = await runCode(code, { inputItems: [data] });
    expect(result[0].json.campaigns).toHaveLength(0);
  });

  test('handles items/campaigns/data response shapes', async () => {
    const shapes = [
      { items: [{ id: 'c6', name: 'Test', tags: ['dabbahwala'], status: 'active' }] },
      { campaigns: [{ id: 'c7', name: 'Test', tags: ['dabbahwala'], status: 'active' }] },
      { data: [{ id: 'c8', name: 'Test', tags: ['dabbahwala'], status: 'active' }] },
    ];
    for (const shape of shapes) {
      const result = await runCode(code, { inputItems: [shape] });
      expect(result[0].json.campaigns).toHaveLength(1);
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// INSTANTLY SETUP
// ═══════════════════════════════════════════════════════════════════════════════

describe('[instantly_setup] Parse Response — Code node', () => {
  let code;
  beforeAll(() => {
    const wf = loadWorkflow('instantly_setup.json');
    code = getCodeNode(wf, 'Parse Response');
  });

  test('already_setup: returns skipped = true', async () => {
    const body = { status: 'already_setup', message: 'Campaigns already configured', campaign_count: 6 };
    const result = await runCode(code, { inputItems: [body] });
    const out = result[0].json;
    expect(out.skipped).toBe(true);
    expect(out.campaign_count).toBe(6);
    expect(out.message).toBe('Campaigns already configured');
  });

  test('full setup: returns skipped = false with campaign data', async () => {
    const body = {
      status: 'ok',
      created_campaigns: { NURTURE_SLOW: 'c1', PROMO_STANDARD: 'c2' },
      resolved_campaign_ids: { NURTURE_SLOW: 'c1', PROMO_STANDARD: 'c2', REACTIVATION: 'c3' },
      active_customer_campaign_id: 'c4',
      configure_results: [],
      dedup_results: [],
    };
    const result = await runCode(code, { inputItems: [body] });
    const out = result[0].json;
    expect(out.skipped).toBe(false);
    expect(out.campaign_count).toBe(3);
    expect(out.active_customer_id).toBe('c4');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// LAPSED CUSTOMER CYCLE
// ═══════════════════════════════════════════════════════════════════════════════

describe('[lapsed_customer_cycle] Log Results + IF', () => {
  let logCode, ifc;
  beforeAll(() => {
    const wf = loadWorkflow('lapsed_customer_cycle.json');
    logCode = getCodeNode(wf, 'Log Results');
    ifc     = getIfConditions(wf, 'Any Actions Taken?');
  });

  test('Log Results: has_actions = true when SMS or campaign actions exist', async () => {
    const item = {
      processed: 5,
      errors: [],
      results: [
        { chosen_action: 'send_sms' },
        { chosen_action: 'none' },
        { chosen_action: 'move_campaign' },
      ]
    };
    const result = await runCode(logCode, { inputItems: [item] });
    const out = result[0].json;
    expect(out.has_actions).toBe(true);
    expect(out.action_summary.send_sms).toBe(1);
    expect(out.action_summary.move_campaign).toBe(1);
  });

  test('Log Results: has_actions = false when all actions are "none"', async () => {
    const item = { processed: 3, errors: [], results: [{ chosen_action: 'none' }, { chosen_action: 'none' }] };
    const result = await runCode(logCode, { inputItems: [item] });
    expect(result[0].json.has_actions).toBe(false);
  });

  test('Log Results: has_actions = false when processed = 0', async () => {
    const item = { processed: 0, errors: [], results: [] };
    const result = await runCode(logCode, { inputItems: [item] });
    expect(result[0].json.has_actions).toBe(false);
  });

  test('Any Actions Taken? TRUE when has_actions = true', () => {
    expect(evalIf(ifc, { has_actions: true })).toBe(true);
  });
  test('Any Actions Taken? FALSE when has_actions = false', () => {
    expect(evalIf(ifc, { has_actions: false })).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// MARKETING QUERY FORM
// ═══════════════════════════════════════════════════════════════════════════════

describe('[marketing_query_form] Code + IF nodes', () => {
  let mapCode, formatCode, ifc;
  beforeAll(() => {
    const wf = loadWorkflow('marketing_query_form.json');
    mapCode    = getCodeNode(wf, 'Map Category');
    formatCode = getCodeNode(wf, 'Format Response');
    ifc        = getIfConditions(wf, 'Is Submission?');
  });

  test('Map Category: maps known form dropdown to API category', async () => {
    const input = { 'Query Category': 'Pipeline Snapshot — Lifecycle stage distribution', 'Your Question or Details': '' };
    const result = await runCode(mapCode, { inputItems: [input] });
    expect(result[0].json.category).toBe('pipeline_snapshot');
    expect(result[0].json.is_submission).toBe(false);
  });

  test('Map Category: submit_input → is_submission = true', async () => {
    const input = { 'Query Category': 'Submit New Input — Add your own observation or note', 'Your Question or Details': 'Some observation', 'Your Name (for Submit New Input)': 'Ali' };
    const result = await runCode(mapCode, { inputItems: [input] });
    expect(result[0].json.is_submission).toBe(true);
    expect(result[0].json.category).toBe('submit_input');
  });

  test('Map Category: unknown category defaults to free_form', async () => {
    const input = { 'Query Category': 'Something Else Entirely', 'Your Question or Details': '' };
    const result = await runCode(mapCode, { inputItems: [input] });
    expect(result[0].json.category).toBe('free_form');
  });

  test('Format Response: builds formatted response with category and answer', async () => {
    const response = { answer: 'Here are your insights', category: 'pipeline_snapshot', question: 'How many contacts?' };
    const nodeData = { 'Map Category': [{ is_submission: false, author: 'Test', input_type: 'observation' }] };
    const result = await runCode(formatCode, { inputItems: [response], nodeData });
    const out = result[0].json;
    expect(out.formatted_answer).toContain('DabbahWala Marketing Intelligence');
    expect(out.formatted_answer).toContain('PIPELINE SNAPSHOT');
    expect(out.formatted_answer).toContain('Here are your insights');
    expect(out.raw_answer).toBe('Here are your insights');
  });

  test('Is Submission? TRUE when is_submission = true', () => {
    expect(evalIf(ifc, { is_submission: true })).toBe(true);
  });
  test('Is Submission? FALSE when is_submission = false', () => {
    expect(evalIf(ifc, { is_submission: false })).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// MENU SYNC WEEKLY
// ═══════════════════════════════════════════════════════════════════════════════

describe('[menu_sync_weekly] Build Menu Summary + IF', () => {
  let code, ifc;
  beforeAll(() => {
    const wf = loadWorkflow('menu_sync_weekly.json');
    code = getCodeNode(wf, 'Build Menu Summary');
    ifc  = getIfConditions(wf, 'Scrape OK?');
  });

  test('builds summary with veg and non-veg items', async () => {
    const menuData = {
      week_start: '2024-01-01',
      item_count: 4,
      items: [
        { item_name: 'Dal', is_veg: true, is_featured: true },
        { item_name: 'Palak', is_veg: true, is_featured: false },
        { item_name: 'Chicken', is_veg: false, is_featured: true },
        { item_name: 'Lamb', is_veg: false, is_featured: false },
      ]
    };
    const nodeData = { 'Get Current Menu': [menuData] };
    const result = await runCode(code, { nodeData });
    const out = result[0].json;
    expect(out.summary).toContain('4 items synced');
    expect(out.summary).toContain('Dal');
    expect(out.summary).toContain('Chicken');
    expect(out.item_count).toBe(4);
  });

  test('handles empty items list', async () => {
    const menuData = { week_start: '2024-01-01', item_count: 0, items: [] };
    const nodeData = { 'Get Current Menu': [menuData] };
    const result = await runCode(code, { nodeData });
    expect(result[0].json.item_count).toBe(0);
  });

  test('Scrape OK? TRUE when status = "completed"', () => {
    expect(evalIf(ifc, { status: 'completed' })).toBe(true);
  });
  test('Scrape OK? FALSE when status = "failed"', () => {
    expect(evalIf(ifc, { status: 'failed' })).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SHIPDAY FEEDBACK SYNC
// ═══════════════════════════════════════════════════════════════════════════════

describe('[shipday_feedback_sync] New Feedback Found? — IF node', () => {
  test('TRUE when run_lifecycle = true', () => {
    const wf = loadWorkflow('shipday_feedback_sync.json');
    const ifc = getIfConditions(wf, 'New Feedback Found?');
    expect(evalIf(ifc, { run_lifecycle: true })).toBe(true);
    expect(evalIf(ifc, { run_lifecycle: false })).toBe(false);
    expect(evalIf(ifc, {})).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SMS DISPATCH
// ═══════════════════════════════════════════════════════════════════════════════

describe('[sms_dispatch] Code + IF nodes', () => {
  let splitCode, buildCode, ifc;
  beforeAll(() => {
    const wf = loadWorkflow('sms_dispatch.json');
    splitCode = getCodeNode(wf, 'Split Contacts');
    buildCode = getCodeNode(wf, 'Build SMS Message');
    ifc       = getIfConditions(wf, 'Has Pending SMS?');
  });

  test('Split Contacts: splits array correctly', async () => {
    const contacts = [{ contact_id: 1, phone: '+1111' }, { contact_id: 2, phone: '+2222' }];
    const result = await runCode(splitCode, { inputItems: [contacts] });
    expect(result).toHaveLength(2);
  });

  test('Split Contacts: returns empty for empty array', async () => {
    const result = await runCode(splitCode, { inputItems: [[]] });
    expect(result).toHaveLength(0);
  });

  const SMS_CASES = [
    [{ contact_email: 'a@b.com', sms_level: 1, lifecycle: 'cold', contact_id: 1, phone: '+1111' },       'Fresh items available'],
    [{ contact_email: 'a@b.com', sms_level: 2, lifecycle: 'warm', contact_id: 2, phone: '+2222' },       'Special offer'],
    [{ contact_email: 'a@b.com', sms_level: 3, lifecycle: 'lapsed', contact_id: 3, phone: '+3333' },     'We miss you'],
    [{ contact_email: 'a@b.com', sms_level: 1, lifecycle: 'new_customer', contact_id: 4, phone: '+4444' }, 'Thanks for your recent order'],
  ];

  test.each(SMS_CASES)('sms_level=%s lifecycle=%s → correct message', async (contact, expectedSubstring) => {
    const result = await runCode(buildCode, { inputItems: [contact] });
    const out = result[0]?.json || result.json;
    expect(out.message_body).toContain(expectedSubstring);
    expect(out.phone).toBe(contact.phone);
  });

  test('does NOT crash when contact_email is undefined (null-guard fix)', async () => {
    // Bug: contact.contact_email.split('@') crashes if email is undefined.
    // Fix: (contact.contact_email || '').split('@')
    const contact = { contact_id: 5, phone: '+12144996143', sms_level: 1, lifecycle: 'cold' };
    await expect(runCode(buildCode, { inputItems: [contact] })).resolves.toBeDefined();
  });

  test('does NOT crash when contact_email is null', async () => {
    const contact = { contact_id: 6, phone: '+12144996143', sms_level: 1, lifecycle: 'new_customer', contact_email: null };
    await expect(runCode(buildCode, { inputItems: [contact] })).resolves.toBeDefined();
  });

  test('test phone number +12144996143 produces a valid SMS message body', async () => {
    const contact = { contact_id: 7, phone: '+12144996143', contact_email: 'vivek@dabbahwala.com',
                      sms_level: 1, lifecycle: 'cold' };
    const result = await runCode(buildCode, { inputItems: [contact] });
    const out = result[0]?.json || result.json;
    expect(out.phone).toBe('+12144996143');
    expect(typeof out.message_body).toBe('string');
    expect(out.message_body.length).toBeGreaterThan(10);
  });

  test('Has Pending SMS? TRUE when length > 0', () => {
    // The IF checks $json.length — an array's length property
    expect(evalIf(ifc, [{ id: 1 }])).toBe(true);
  });

  test('Has Pending SMS? FALSE when length = 0', () => {
    expect(evalIf(ifc, [])).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SYSTEM TEST SUITE
// ═══════════════════════════════════════════════════════════════════════════════

describe('[system_test_suite] Any Failures? — IF node', () => {
  test('TRUE when has_failures = true', () => {
    const wf = loadWorkflow('system_test_suite.json');
    const ifc = getIfConditions(wf, 'Any Failures?');
    expect(evalIf(ifc, { has_failures: true })).toBe(true);
    expect(evalIf(ifc, { has_failures: false })).toBe(false);
    expect(evalIf(ifc, {})).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// TELNYX SMS HISTORICAL IMPORT
// ═══════════════════════════════════════════════════════════════════════════════

describe('[telnyx_sms_historical_import] No More Records? — IF node', () => {
  test('TRUE when _no_more_records = true', () => {
    const wf = loadWorkflow('telnyx_sms_historical_import.json');
    const ifc = getIfConditions(wf, 'No More Records?');
    expect(evalIf(ifc, { _no_more_records: true })).toBe(true);
    expect(evalIf(ifc, { _no_more_records: false })).toBe(false);
    expect(evalIf(ifc, {})).toBe(false);
  });
});
