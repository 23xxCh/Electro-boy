import fs from 'node:fs';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createEnergyIsland, fitPanelsToGeneratedRoof } from '../scene.js';

const bytes = fs.readFileSync(new URL('../../../public/energy-world/lux3d-house-4166575.glb', import.meta.url));
const jsonLength = bytes.readUInt32LE(12);
const gltf = JSON.parse(bytes.subarray(20, 20 + jsonLength));
const binaryStart = 28 + jsonLength;
const primitive = gltf.meshes[0].primitives[0];
function read(index) {
  const a = gltf.accessors[index], b = gltf.bufferViews[a.bufferView];
  const width = { SCALAR: 1, VEC3: 3 }[a.type], size = { 5126: 4, 5125: 4, 5123: 2 }[a.componentType];
  assert.ok(width && size);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const offset = binaryStart + (b.byteOffset ?? 0) + (a.byteOffset ?? 0), result = [];
  for (let i = 0; i < a.count; i++) for (let k = 0; k < width; k++) {
    const p = offset + i * (b.byteStride ?? size * width) + k * size;
    result.push(a.componentType === 5126 ? view.getFloat32(p, true) : a.componentType === 5125 ? view.getUint32(p, true) : view.getUint16(p, true));
  }
  return result;
}
const geometry = new THREE.BufferGeometry();
geometry.setAttribute('position', new THREE.Float32BufferAttribute(read(primitive.attributes.POSITION), 3));
geometry.setIndex(read(primitive.indices));
const house = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ side: gltf.materials[primitive.material]?.doubleSided ? THREE.DoubleSide : THREE.FrontSide }));
const bounds = new THREE.Box3().setFromObject(house), size = bounds.getSize(new THREE.Vector3()), center = bounds.getCenter(new THREE.Vector3());
house.scale.set(4.45 / size.x, 3.03 / size.y, 3.2 / size.z);
house.position.set(-center.x * house.scale.x, .43 - bounds.min.y * house.scale.y, -.42 - center.z * house.scale.z);
house.updateMatrixWorld(true);
const island = createEnergyIsland(), fit = fitPanelsToGeneratedRoof(house, island.devices.pv);
assert.ok(fit);
const ray = new THREE.Raycaster(), point = new THREE.Vector3();
let min = Infinity, max = -Infinity, hits = 0, misses = 0;
island.devices.pv.traverse(part => {
  const positions = part.geometry?.getAttribute('position');
  if (!positions) return;
  for (let i = 0; i < positions.count; i++) {
    point.fromBufferAttribute(positions, i).applyMatrix4(part.matrixWorld);
    ray.set(new THREE.Vector3(point.x, 6, point.z), new THREE.Vector3(0, -1, 0));
    const hit = ray.intersectObject(house)[0];
    if (hit) { min = Math.min(min, point.y - hit.point.y); max = Math.max(max, point.y - hit.point.y); hits++; }
    else misses++;
  }
});
assert.ok(min >= .005, `PV intersects roof: ${min}`);
assert.ok(max <= .15, `PV floats above roof: ${max}`);
assert.equal(misses, 0);
console.log(JSON.stringify({ status: 'pass', slopeDegrees: fit.angle * 180 / Math.PI, minimumRoofClearance: min, maximumVertexClearance: max, sampledVertices: hits, misses, units: 'scene units, not installed dimensions', scope: 'exact GLB geometry placement; rendered acceptance remains separate' }));
