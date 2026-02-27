'use strict';
/**
 * Tests for [Chatbot] Docs Sync — google_docs_sync.json
 *
 * Covers:
 *  - Code: Split Files              — extracts files array into individual items
 *  - Code: Filter & Classify        — filters non-Google-Docs, classifies by title
 *  - Code: Build Document Payload   — constructs sync payload from doc + meta
 *  - IF:   Has Docs?                — TRUE (_empty != true) / FALSE (_empty = true)
 *  - HTTP: Sync to API              — POST to /api/team-content/sync
 */

const { loadWorkflow, getCodeNode, getIfConditions, getNode, runCode, evalIf } = require('../helpers/runtime');

let wf, splitCode, filterCode, buildCode, ifc;
beforeAll(() => {
  wf          = loadWorkflow('google_docs_sync.json');
  splitCode   = getCodeNode(wf, 'Split Files');
  filterCode  = getCodeNode(wf, 'Filter & Classify');
  buildCode   = getCodeNode(wf, 'Build Document Payload');
  ifc         = getIfConditions(wf, 'Has Docs?');
});

// ─── Split Files — Code node ─────────────────────────────────────────────────

describe('Split Files — Code node', () => {
  test('returns one item per file', async () => {
    const data = { files: [{ id: 'f1' }, { id: 'f2' }, { id: 'f3' }] };
    const items = await runCode(splitCode, { inputItems: [{ json: data }] });
    expect(items).toHaveLength(3);
    expect(items[0].json.id).toBe('f1');
  });

  test('returns empty array when no files', async () => {
    const items = await runCode(splitCode, { inputItems: [{ json: { files: [] } }] });
    expect(items).toHaveLength(0);
  });

  test('handles missing files key', async () => {
    const items = await runCode(splitCode, { inputItems: [{ json: {} }] });
    expect(items).toHaveLength(0);
  });
});

// ─── Filter & Classify — Code node ───────────────────────────────────────────

describe('Filter & Classify — Code node', () => {
  const googleDoc = {
    id: 'doc123',
    mimeType: 'application/vnd.google-apps.document',
    name: 'Weekly Ground Notes',
    modifiedTime: '2026-01-01T00:00:00Z',
    lastModifyingUser: { displayName: 'Vivek' },
  };

  test('classifies as ground_note by default', async () => {
    const [out] = await runCode(filterCode, { inputItems: [{ json: googleDoc }] });
    expect(out.json.content_type).toBe('ground_note');
    expect(out.json.google_doc_id).toBe('doc123');
  });

  test('classifies as ad_copy when title contains "ad copy"', async () => {
    const doc = { ...googleDoc, name: 'Facebook Ad Copy — January' };
    const [out] = await runCode(filterCode, { inputItems: [{ json: doc }] });
    expect(out.json.content_type).toBe('ad_copy');
  });

  test('classifies as ad_copy when title contains "social media"', async () => {
    const doc = { ...googleDoc, name: 'Social Media Posts Feb 2026' };
    const [out] = await runCode(filterCode, { inputItems: [{ json: doc }] });
    expect(out.json.content_type).toBe('ad_copy');
  });

  test('filters out non-Google-Docs (returns empty)', async () => {
    const pdf = { id: 'p1', mimeType: 'application/pdf', name: 'Report.pdf' };
    const items = await runCode(filterCode, { inputItems: [{ json: pdf }] });
    expect(items).toHaveLength(0);
  });

  test('builds doc_url correctly', async () => {
    const [out] = await runCode(filterCode, { inputItems: [{ json: googleDoc }] });
    expect(out.json.doc_url).toContain('doc123');
    expect(out.json.doc_url).toContain('docs.google.com');
  });
});

// ─── Build Document Payload — Code node ──────────────────────────────────────

describe('Build Document Payload — Code node', () => {
  const meta = {
    google_doc_id: 'doc123',
    content_type: 'ground_note',
    title: 'Ground Notes Week 1',
    google_last_modified: '2026-01-01T00:00:00Z',
    author: 'Vivek',
    doc_url: 'https://docs.google.com/document/d/doc123/edit',
  };

  test('extracts text from docContent.text', async () => {
    const docContent = { text: 'Hello world content.' };
    const nodeData = { 'Filter & Classify': [{ json: meta }] };
    const [out] = await runCode(buildCode, { inputItems: [{ json: docContent }], nodeData });
    expect(out.json.body).toBe('Hello world content.');
    expect(out.json.google_doc_id).toBe('doc123');
    expect(out.json.content_type).toBe('ground_note');
    expect(out.json.title).toBe('Ground Notes Week 1');
    expect(out.json.tags).toEqual([]);
  });

  test('extracts text from body.content paragraphs', async () => {
    const docContent = {
      body: {
        content: [
          { paragraph: { elements: [{ textRun: { content: 'Para 1 ' } }, { textRun: { content: 'text.' } }] } },
          { paragraph: { elements: [{ textRun: { content: 'Para 2.' } }] } },
        ],
      },
    };
    const nodeData = { 'Filter & Classify': [{ json: meta }] };
    const [out] = await runCode(buildCode, { inputItems: [{ json: docContent }], nodeData });
    expect(out.json.body).toContain('Para 1');
    expect(out.json.body).toContain('Para 2');
  });
});

// ─── Has Docs? — IF node ─────────────────────────────────────────────────────

describe('Has Docs? — IF node', () => {
  test('TRUE: _empty is not true → has docs', () => {
    expect(evalIf(ifc, { _empty: false })).toBe(true);
  });
  test('TRUE: _empty undefined → has docs', () => {
    expect(evalIf(ifc, { google_doc_id: 'doc1' })).toBe(true);
  });
  test('FALSE: _empty = true → no docs', () => {
    expect(evalIf(ifc, { _empty: true })).toBe(false);
  });
});

// ─── HTTP node config ─────────────────────────────────────────────────────────

describe('Sync to API — HTTP node', () => {
  test('method is POST', () => {
    expect(getNode(wf, 'Sync to API').parameters.method).toBe('POST');
  });
  test('URL targets /api/team-content/sync', () => {
    expect(getNode(wf, 'Sync to API').parameters.url).toContain('/team-content/sync');
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('has a schedule trigger', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeDefined();
  });
  test('has No New Docs — Skip terminal node', () => {
    expect(getNode(wf, 'No New Docs — Skip')).toBeDefined();
  });
});
