'use strict';
/**
 * n8n test runtime helpers.
 *
 * Provides:
 *   loadWorkflow(filename)      — loads a workflow JSON from n8n/
 *   getCodeNode(wf, name)       — returns the jsCode string of a Code node
 *   getIfConditions(wf, name)   — returns the conditions array of an IF node
 *   getSwitchRules(wf, name)    — returns the rules array of a Switch node
 *   runCode(code, ctx)          — executes jsCode in an n8n-like sandbox
 *   evalIf(conditions, json)    — evaluates IF conditions against a JSON object
 *   evalSwitch(rules, json)     — evaluates Switch rules and returns the matched output index
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const N8N_DIR = path.resolve(__dirname, '../../../n8n');

// ─── Workflow loader ──────────────────────────────────────────────────────────

function loadWorkflow(filename) {
  const filePath = path.join(N8N_DIR, filename);
  const raw = fs.readFileSync(filePath, 'utf8');
  return JSON.parse(raw);
}

// ─── Node extractors ──────────────────────────────────────────────────────────

function getCodeNode(wf, name) {
  const node = wf.nodes.find(
    n => n.type === 'n8n-nodes-base.code' && n.name === name
  );
  if (!node) throw new Error(`Code node "${name}" not found`);
  return node.parameters.jsCode || node.parameters.code || '';
}

function getIfConditions(wf, name) {
  const node = wf.nodes.find(
    n => n.type === 'n8n-nodes-base.if' && n.name === name
  );
  if (!node) throw new Error(`IF node "${name}" not found`);
  return node.parameters.conditions || { conditions: [] };
}

function getSwitchRules(wf, name) {
  const node = wf.nodes.find(
    n => n.type === 'n8n-nodes-base.switch' && n.name === name
  );
  if (!node) throw new Error(`Switch node "${name}" not found`);
  return (node.parameters.rules && node.parameters.rules.values) || [];
}

// ─── Code node runner ─────────────────────────────────────────────────────────

/**
 * Runs a Code node's jsCode in a sandboxed context.
 *
 * ctx:
 *   inputItems   — array of objects (becomes $input.all() items)
 *   nodeData     — { 'NodeName': [ {json: ...}, ... ] }  (for $('NodeName').item.json)
 *
 * Returns the items that the code returned.
 */
async function runCode(jsCode, ctx = {}) {
  const inputItems = (ctx.inputItems || []).map(i => {
    if (i == null) return { json: null };
    return (typeof i === 'object' && typeof i.json !== 'undefined') ? i : { json: i };
  });
  const nodeData = ctx.nodeData || {};

  // Build the n8n API surface used by Code nodes
  const $input = {
    all: () => inputItems,
    first: () => inputItems[0] || null,
    item: inputItems[0] || { json: {} },
  };

  // $('NodeName') or $node['NodeName'] — lookup helper
  const _lookupNode = (nodeName) => {
    const rows = nodeData[nodeName] || [];
    const items = rows.map(r => (typeof r.json !== 'undefined' ? r : { json: r }));
    return {
      item: items[0] || { json: {} },
      all: () => items,
      first: () => items[0] || { json: {} },
    };
  };

  // $node['NodeName'] — property access style
  const $node = new Proxy({}, {
    get(_, nodeName) {
      return _lookupNode(nodeName);
    },
  });

  // $('NodeName') — function call style used in Code nodes (most common in n8n)
  const $proxy = function(nodeName) {
    return _lookupNode(nodeName);
  };

  // Build the sandbox
  const sandbox = {
    $input,
    $json: ($input.item || { json: {} }).json,
    $: $proxy,
    $node,
    console,
    // items is the legacy n8n API used in some older code nodes
    items: inputItems,
    require,
  };

  // Wrap code: Code nodes in n8n return items or single item
  const wrapped = `
    (async function() {
      ${jsCode}
    })()
  `;

  let result;
  try {
    const script = new vm.Script(wrapped);
    result = await script.runInNewContext(sandbox);
  } catch (err) {
    throw new Error(`Code node execution error: ${err.message}\n\nCode:\n${jsCode.slice(0, 300)}`);
  }

  // Normalise: code can return a single item, an array, or nothing
  if (!result) return [{ json: {} }];
  if (Array.isArray(result)) {
    return result.map(r => (typeof r.json !== 'undefined' ? r : { json: r }));
  }
  return [typeof result.json !== 'undefined' ? result : { json: result }];
}

// ─── IF evaluator ─────────────────────────────────────────────────────────────

/**
 * Evaluates n8n IF conditions against a plain JS object.
 * Returns true if ALL conditions (combinator=and) pass, false otherwise.
 *
 * Supports operators: equals, notEquals, gt, gte, lt, lte, contains,
 *   startsWith, endsWith, exists, notExists, empty, notEmpty
 */
function evalIf(conditionsObj, json, nodeData) {
  const conditions = (conditionsObj.conditions || conditionsObj) ;
  const conds = Array.isArray(conditions) ? conditions
    : (conditions.conditions || []);
  const combinator = conditionsObj.combinator || 'and';

  if (conds.length === 0) return false;

  const results = conds.map(c => _evalCondition(c, json, nodeData));
  if (combinator === 'or') return results.some(Boolean);
  return results.every(Boolean);
}

function _resolveValue(expr, json, nodeData) {
  if (typeof expr !== 'string') return expr;
  // n8n expression: ={{ ... }}
  const m = expr.match(/^=\{\{([\s\S]*)\}\}$/);
  if (!m) return expr;
  const code = m[1].trim();
  // Build a $() function for node lookups
  const _nd = nodeData || {};
  const $fn = function(nodeName) {
    const rows = _nd[nodeName] || [];
    const items = rows.map(r => (typeof r.json !== 'undefined' ? r : { json: r }));
    return {
      item: items[0] || { json: {} },
      all: () => items,
      first: () => items[0] || { json: {} },
    };
  };
  try {
    const fn = new Function('$json', '$', `return (${code})`); // eslint-disable-line no-new-func
    return fn(json, $fn);
  } catch (e) {
    return undefined;
  }
}

function _evalCondition(c, json, nodeData) {
  const left  = _resolveValue(c.leftValue, json, nodeData);
  const right = _resolveValue(c.rightValue, json, nodeData);
  const op    = c.operator || {};

  switch (op.operation) {
    case 'equals':      return left === right;
    case 'notEquals':
    case 'notEqual':    return left !== right;
    case 'gt':          return Number(left) > Number(right);
    case 'gte':         return Number(left) >= Number(right);
    case 'lt':          return Number(left) < Number(right);
    case 'lte':         return Number(left) <= Number(right);
    case 'contains':    return String(left).includes(String(right));
    case 'notContains': return !String(left).includes(String(right));
    case 'startsWith':  return String(left).startsWith(String(right));
    case 'endsWith':    return String(left).endsWith(String(right));
    case 'exists':      return left !== undefined && left !== null;
    case 'notExists':   return left === undefined || left === null;
    case 'empty':       return !left;
    case 'notEmpty':    return !!left;
    case 'true':        return left === true;
    case 'false':       return left === false;
    default:
      // Fallback: truthy check
      return !!left;
  }
}

// ─── Switch evaluator ─────────────────────────────────────────────────────────

/**
 * Evaluates Switch node rules against a JSON object.
 * Returns the outputKey string of the matched rule, or the 0-based index if outputKey
 * is not set, or -1 if none matched (fallback output).
 *
 * n8n Switch nodes in modern versions set outputKey on each rule.
 */
function evalSwitch(rules, json) {
  for (let i = 0; i < rules.length; i++) {
    const rule = rules[i];
    const condObj = rule.conditions || { conditions: [{ leftValue: rule.value, rightValue: json[rule.field], operator: { operation: 'equals' } }] };
    if (evalIf(condObj, json)) {
      return rule.outputKey !== undefined ? rule.outputKey : i;
    }
  }
  return 'fallback'; // fallback output
}

/**
 * Get any node by name from a workflow (regardless of type).
 */
function getNode(wf, name) {
  const node = wf.nodes.find(n => n.name === name);
  if (!node) throw new Error(`Node "${name}" not found in workflow`);
  return node;
}

module.exports = {
  loadWorkflow,
  getCodeNode,
  getIfConditions,
  getSwitchRules,
  getNode,
  runCode,
  evalIf,
  evalSwitch,
};
