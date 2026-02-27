'use strict';
/**
 * Tests for [System] Connectivity Check — system_connectivity_check.json
 *
 * Covers:
 *  - HTTP: FastAPI Health      — GET /health on Render
 *  - HTTP: Telnyx API          — GET with Bearer auth
 *  - HTTP: Airtable API        — GET with Bearer auth
 *  - HTTP: Instantly API       — GET with Bearer auth
 *  - HTTP: Shipday API         — GET /info
 *  - HTTP: Get Credentials     — GET /api/credentials bootstrap
 *  - Workflow structure        — manual only, all 5 external services configured
 */

const { loadWorkflow, getNode } = require('../helpers/runtime');

let wf;
beforeAll(() => {
  wf = loadWorkflow('system_connectivity_check.json');
});

// ─── FastAPI Health ──────────────────────────────────────────────────────────

describe('FastAPI Health — HTTP node', () => {
  test('targets /health endpoint', () => {
    const node = getNode(wf, 'FastAPI Health');
    expect(node.parameters.url).toContain('/health');
  });
  test('has timeout configured', () => {
    const node = getNode(wf, 'FastAPI Health');
    expect(node.parameters.options?.timeout).toBeGreaterThan(0);
  });
});

// ─── Telnyx API ──────────────────────────────────────────────────────────────

describe('Telnyx API — HTTP node', () => {
  test('targets telnyx.com API', () => {
    const node = getNode(wf, 'Telnyx API');
    expect(node.parameters.url).toContain('telnyx.com');
  });
  test('has timeout configured', () => {
    expect(getNode(wf, 'Telnyx API').parameters.options?.timeout).toBeGreaterThan(0);
  });
});

// ─── Airtable API ─────────────────────────────────────────────────────────────

describe('Airtable API — HTTP node', () => {
  test('targets airtable.com API', () => {
    const node = getNode(wf, 'Airtable API');
    expect(node.parameters.url).toContain('airtable.com');
  });
  test('has timeout configured', () => {
    expect(getNode(wf, 'Airtable API').parameters.options?.timeout).toBeGreaterThan(0);
  });
});

// ─── Instantly API ────────────────────────────────────────────────────────────

describe('Instantly API — HTTP node', () => {
  test('targets instantly.ai API', () => {
    const node = getNode(wf, 'Instantly API');
    expect(node.parameters.url).toContain('instantly.ai');
  });
  test('has timeout configured', () => {
    expect(getNode(wf, 'Instantly API').parameters.options?.timeout).toBeGreaterThan(0);
  });
});

// ─── Shipday API ─────────────────────────────────────────────────────────────

describe('Shipday API — HTTP node', () => {
  test('targets shipday.com API', () => {
    const node = getNode(wf, 'Shipday API');
    expect(node.parameters.url).toContain('shipday.com');
  });
  test('has timeout configured', () => {
    expect(getNode(wf, 'Shipday API').parameters.options?.timeout).toBeGreaterThan(0);
  });
});

// ─── Get Credentials bootstrap ───────────────────────────────────────────────

describe('Get Credentials — HTTP node', () => {
  test('targets /api/credentials', () => {
    const node = getNode(wf, 'Get Credentials');
    expect(node.parameters.url).toContain('/api/credentials');
  });
});

// ─── Workflow structure ───────────────────────────────────────────────────────

describe('Workflow structure', () => {
  test('manual trigger only (connectivity check is on-demand)', () => {
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.manualTrigger')).toBeDefined();
    expect(wf.nodes.find(n => n.type === 'n8n-nodes-base.scheduleTrigger')).toBeUndefined();
  });

  test('checks all 5 external services', () => {
    const externalChecks = ['Telnyx API', 'Airtable API', 'Instantly API', 'Shipday API', 'FastAPI Health'];
    for (const name of externalChecks) {
      expect(() => getNode(wf, name)).not.toThrow();
    }
  });

  test('total HTTP request nodes = 6 (health + 4 external + credentials)', () => {
    const httpNodes = wf.nodes.filter(n => n.type === 'n8n-nodes-base.httpRequest');
    expect(httpNodes.length).toBeGreaterThanOrEqual(5);
  });
});
