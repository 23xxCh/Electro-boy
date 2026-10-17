import assert from 'node:assert/strict';
import { createEnergyIsland, energyFlowState } from '../scene.js';

const base = { kind: 'observation', motion: 'none', pvKw: 0, loadKw: .8, batteryKw: 0, gridImportKw: .8, gridExportKw: 0, socPercent: 70 };
const empty = energyFlowState(null);
assert.equal(empty.soc, null);
assert.equal(empty.moving, false);
assert.ok(empty.flows.every(flow => flow.power === null));
assert.equal(energyFlowState({ ...base, socPercent: 0 }).soc, 0);
assert.equal(energyFlowState({ ...base, socPercent: null }).soc, null);
assert.equal(energyFlowState({ ...base, socPercent: NaN }).soc, null);
assert.equal(energyFlowState({ ...base, motion: 'preview' }).moving, false);
const preview = energyFlowState({ ...base, kind: 'preview', motion: 'preview', batteryKw: -.948 });
assert.equal(preview.moving, true);
assert.equal(preview.preview, true);
assert.deepEqual(preview.flows.find(flow => flow.key === 'battery'), { key: 'battery', power: .948, reverse: true });
assert.equal(preview.flows.find(flow => flow.key === 'pv').power, 0);
assert.equal(energyFlowState({ ...base, kind: 'preview', motion: 'preview' }, true).moving, false);
assert.equal(energyFlowState({ ...base, kind: 'actual', motion: 'receipt_replay' }).moving, true);
assert.equal(energyFlowState({ ...base, kind: 'actual', motion: 'none' }).moving, false);
assert.equal(energyFlowState({ ...base, batteryKw: 1 }).flows.find(flow => flow.key === 'battery').reverse, false);
assert.equal(energyFlowState({ ...base, gridImportKw: null }).flows.find(flow => flow.key === 'gridImport').power, null);
assert.equal(energyFlowState({ ...base, pvKw: -1 }).flows.find(flow => flow.key === 'pv').power, null);
const island = createEnergyIsland();
let triangles = 0, meshes = 0;
island.root.traverse(object => {
  if (!object.geometry) return;
  triangles += (object.geometry.index?.count ?? object.geometry.attributes.position.count) / 3;
  meshes++;
  assert.ok([...object.geometry.attributes.position.array].every(Number.isFinite));
});
assert.ok(triangles < 80000);
assert.ok(meshes < 70);
assert.equal(island.batteryFill.visible, false);
assert.ok(island.houseShell.children.length > 0);
for (const key of ['pv', 'battery', 'grid']) {
  assert.equal(island.devices[key].userData.device, key);
  assert.ok(island.devices[key].children.length > 0);
}
console.log(JSON.stringify({ status: 'pass', scope: 'flow semantics and procedural geometry only, not WebGL/FPS', triangles, meshes }));
