// Independent parser/transport assertions in a VM with no real fetch or module access.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, '../../frontend');
const require = createRequire(path.join(frontend, 'package.json'));
const ts = require('typescript');
const source = fs.readFileSync(path.join(frontend, 'src/assistantWeb.ts'), 'utf8');
const calls = [];
const exports = {};
const fixture = {
  schemaVersion: 'environment-pipeline-v1', environment: 'release_official_ha_unverified', status: 'partial', reasonCode: 'release_ha_fields_missing_or_invalid', householdId: 'site_1',
  validation: { connectedTested: false, identityVerified: false, timeBasisVerified: false },
  deviceTime: '2026-10-17T12:15:00', timeBasis: 'device_local_naive', observedAt: 1792210500,
  units: { power: 'kW', soc: '%', controlPower: 'W' },
  observation: { socPercent: 70, pvKw: 0, loadKw: .55, chargingKw: .4, dischargingKw: 0, batteryActualKw: -.4, gridImportKw: null, gridExportKw: .3 },
  control: { enabled: false, reasonCode: 'release_execution_contract_unverified', operatingMode: 'third_party_control', gridFlow: 'charge', powerControlW: 400, targetSemantics: 'battery_total_charge_or_grid_export' },
  fieldSources: { device: 'official_ha', prices: null }, prices: null,
};
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
const context = vm.createContext({ exports, AbortController, clearTimeout, window: { setTimeout },
  fetch: async (url, options) => {
    calls.push({ url, options });
    assert.equal(url, '/api/assistant/environment');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.method, undefined);
    return { ok: true, json: async () => structuredClone(fixture) };
  },
});
vm.runInContext(compiled.outputText, context, { filename: 'assistantWeb.ts', timeout: 1000 });
const checked = [];
async function check(name, action) { await action(); checked.push(name); }
await check('module load has no fetch; request is fixed same-origin GET', async () => {
  assert.equal(calls.length, 0);
  const data = await exports.getEnvironment();
  assert.equal(data.environment, 'release_official_ha_unverified');
  assert.equal(calls.length, 1);
});
await check('zero and missing remain distinct; signed battery actual is allowed', () => {
  const data = exports.readEnvironment(structuredClone(fixture));
  assert.equal(data.observation.pvKw, 0);
  assert.equal(data.observation.gridImportKw, null);
  assert.equal(data.observation.batteryActualKw, -.4);
});
await check('nonnegative channels and 0..5000 W target enforce the wire contract', () => {
  for (const field of ['pvKw', 'loadKw', 'chargingKw', 'dischargingKw', 'gridImportKw', 'gridExportKw']) {
    const value = structuredClone(fixture); value.observation[field] = -.01;
    assert.throws(() => exports.readEnvironment(value), field);
  }
  for (const power of [-1, 5000.1]) {
    const value = structuredClone(fixture); value.control.powerControlW = power;
    assert.throws(() => exports.readEnvironment(value));
  }
});
await check('full naive timestamp is required without inventing midnight or timezone', () => {
  for (const stamp of ['2026-10-17', '2026-10-17T12:15', '2026-W42-6', '2026-10-17T12:15:00Z', '2026-02-30T12:15:00', '2026-10-17T12:60:00']) {
    const value = structuredClone(fixture); value.deviceTime = stamp;
    assert.throws(() => exports.readEnvironment(value), stamp);
  }
});
await check('wrong local origin, positive validation or enabled control fails closed', () => {
  for (const edit of [value => value.environment = 'local_debug1_sim', value => value.validation.identityVerified = true, value => value.control.enabled = true]) {
    const value = structuredClone(fixture); edit(value);
    assert.throws(() => exports.readEnvironment(value));
  }
});
await check('only explicit read handler invokes getEnvironment', () => {
  const raw = fs.readFileSync(path.join(frontend, 'src/ConsumerHome.tsx'), 'utf8');
  const tree = ts.createSourceFile('ConsumerHome.tsx', raw, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TSX);
  const ancestors = [];
  let count = 0;
  function visit(node) {
    ancestors.push(node);
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'getEnvironment') {
      count += 1;
      const fn = ancestors.findLast(parent => ts.isFunctionDeclaration(parent));
      assert.equal(fn?.name?.text, 'readEnvironmentData');
    }
    ts.forEachChild(node, visit);
    ancestors.pop();
  }
  visit(tree);
  assert.equal(count, 1);
});
const report = { kind: 'independent_offline_frontend_parser', testsRun: checked.length, checks: checked, realFetchCalls: 0, fakeFetchCalls: calls.length,
  sourceHashes: Object.fromEntries(['assistantWeb.ts', 'ConsumerHome.tsx', 'consumer.css'].map(name => [name, createHash('sha256').update(fs.readFileSync(path.join(frontend, 'src', name))).digest('hex')])),
  limitations: 'Pure VM parser/fetch mock and AST review only; no browser, service or remote connectivity.' };
fs.writeFileSync(path.join(here, 'frontend-result.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify({ passed: checked.length, realFetchCalls: 0, fakeFetchCalls: calls.length }));
