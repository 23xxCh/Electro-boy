import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const finite = value => typeof value === 'number' && Number.isFinite(value);
const positive = value => finite(value) && value >= 0 ? value : null;

/** Visual movement is permitted only by the parent frame, never inferred from a target. */
export function energyFlowState(frame, reducedMotion = false) {
  const moving = !reducedMotion && !!frame && (
    frame.kind === 'preview' && frame.motion === 'preview' ||
    frame.kind === 'actual' && frame.motion === 'receipt_replay'
  );
  const battery = finite(frame?.batteryKw) ? frame.batteryKw : null;
  return {
    moving,
    preview: frame?.kind === 'preview',
    soc: finite(frame?.socPercent) && frame.socPercent >= 0 && frame.socPercent <= 100 ? frame.socPercent : null,
    flows: [
      { key: 'pv', power: positive(frame?.pvKw), reverse: false },
      { key: 'load', power: positive(frame?.loadKw), reverse: false },
      { key: 'battery', power: battery === null ? null : Math.abs(battery), reverse: battery !== null && battery < 0 },
      { key: 'gridImport', power: positive(frame?.gridImportKw), reverse: false },
      { key: 'gridExport', power: positive(frame?.gridExportKw), reverse: true },
    ],
  };
}

const material = (color, options = {}) => new THREE.MeshStandardMaterial({ color, roughness: .62, ...options });

function addPart(group, geometry, mat, at, rotation = null) {
  const mesh = new THREE.Mesh(geometry, mat);
  mesh.position.set(...at);
  if (rotation) mesh.rotation.set(...rotation);
  group.add(mesh);
  return mesh;
}

function batch(group) {
  group.updateMatrixWorld(true);
  const buckets = new Map();
  for (const mesh of [...group.children]) {
    const list = buckets.get(mesh.material) ?? [];
    const geometry = mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry.clone();
    list.push(geometry.applyMatrix4(mesh.matrixWorld));
    buckets.set(mesh.material, list);
    mesh.geometry.dispose();
    group.remove(mesh);
  }
  for (const [mat, geometries] of buckets) {
    const merged = mergeGeometries(geometries);
    geometries.forEach(geometry => geometry.dispose());
    const mesh = new THREE.Mesh(merged, mat);
    group.add(mesh);
  }
}

/** Local original geometry: it is a stylised illustration, not a scanned installation. */
export function createEnergyIsland() {
  const root = new THREE.Group();
  root.name = 'EW01-procedural-energy-island';
  const architecture = new THREE.Group();
  const houseShell = new THREE.Group();
  houseShell.name = 'procedural-house-shell';
  const devices = Object.fromEntries(['pv', 'battery', 'grid'].map(key => {
    const group = new THREE.Group();
    group.name = key;
    group.userData.device = key;
    root.add(group);
    return [key, group];
  }));
  root.add(architecture, houseShell);
  const mats = {
    rock: material('#232b4a', { flatShading: true, roughness: 1 }),
    rockLight: material('#3e476a', { flatShading: true }),
    lawn: material('#2c655c'),
    concrete: material('#ded4be'),
    edge: material('#778d9e', { metalness: .3 }),
    dark: material('#172d40', { metalness: .15 }),
    wall: material('#e8dcc4'),
    wood: material('#ad7850'),
    floor: material('#d2ac7f'),
    upholstery: material('#ead5af'),
    rust: material('#b96450'),
    green: material('#427d59', { flatShading: true }),
    leaf: material('#79a16a', { flatShading: true }),
    glass: material('#83b8bd', { transparent: true, opacity: .24, metalness: .2, depthWrite: false }),
    lamp: material('#ffcb83', { emissive: '#ffb860', emissiveIntensity: .75 }),
    panel: material('#132f53', { metalness: .55, roughness: .26 }),
    panelFrame: material('#91b7ca', { metalness: .6, roughness: .35 }),
    shell: material('#e0e8ed', { metalness: .2, roughness: .36 }),
    teal: material('#76e9d3', { emissive: '#3cb8af', emissiveIntensity: .22 }),
    eye: material('#56d4ee', { emissive: '#28bbdd', emissiveIntensity: .7 }),
  };
  const box = (group, size, mat, at, rotation) => addPart(group, new THREE.BoxGeometry(...size), mat, at, rotation);
  const cylinder = (group, radii, height, mat, at, sides = 12, rotation) => addPart(group, new THREE.CylinderGeometry(radii[0], radii[1], height, sides), mat, at, rotation);
  const sphere = (group, radius, mat, at, scale = null) => {
    const mesh = addPart(group, new THREE.SphereGeometry(radius, 12, 8), mat, at);
    if (scale) mesh.scale.set(...scale);
    return mesh;
  };

  const rock = cylinder(architecture, [4.7, 1.2], 2.15, mats.rock, [0, -1.1, 0], 9);
  rock.scale.z = .79;
  const shelf = cylinder(architecture, [4.72, 4.5], .28, mats.rockLight, [0, -.06, 0], 9);
  shelf.scale.z = .79;
  const grass = cylinder(architecture, [4.6, 4.6], .11, mats.lawn, [0, .14, 0], 9);
  grass.scale.z = .79;
  for (let i = 0; i < 7; i++) {
    const angle = i / 7 * Math.PI * 2;
    const boulder = addPart(architecture, new THREE.IcosahedronGeometry(.54, 0), i % 2 ? mats.rockLight : mats.rock, [Math.cos(angle) * 3.7, -.4 - i % 3 * .2, Math.sin(angle) * 2.9]);
    boulder.scale.set(1.1, .85, .9);
  }
  box(architecture, [5.12, .18, 4.05], mats.concrete, [0, .3, -.25]);
  box(houseShell, [4.35, .17, 3.2], mats.floor, [0, .44, -.42]);
  box(houseShell, [4.35, .16, 3.2], mats.floor, [0, 1.9, -.42]);
  box(houseShell, [4.35, 2.75, .14], mats.wall, [0, 1.85, -1.98]);
  box(houseShell, [.14, 2.75, 3.2], mats.wall, [-2.2, 1.85, -.42]);
  box(houseShell, [.1, 2.75, 1.05], mats.wall, [2.2, 1.85, -1.5]);
  for (const x of [-2.1, .55, 2.1]) box(houseShell, [.11, 2.85, .11], mats.wood, [x, 1.89, 1.12]);
  box(houseShell, [4.4, .12, .14], mats.wood, [0, 3.27, 1.13]);
  box(houseShell, [.07, 2.7, 1.55], mats.glass, [2.18, 1.85, .32]);
  box(houseShell, [4.9, .18, 3.75], mats.dark, [0, 3.42, -.4], [.14, 0, 0]);
  box(houseShell, [4.94, .06, .08], mats.edge, [0, 3.18, 1.45]);

  // Furniture is decorative; the only observed house value is total load.
  box(architecture, [1.9, .13, 1.05], mats.rust, [-.15, .55, .05]);
  box(architecture, [1.55, .32, .62], mats.upholstery, [-.2, .79, -.48]);
  box(architecture, [1.55, .47, .15], mats.upholstery, [-.2, 1.02, -.75]);
  box(architecture, [.2, .38, .65], mats.upholstery, [-.99, .83, -.48]);
  box(architecture, [.2, .38, .65], mats.upholstery, [.59, .83, -.48]);
  cylinder(architecture, [.42, .42], .07, mats.wood, [-.25, .8, .45]);
  cylinder(architecture, [.08, .13], .29, mats.dark, [-.25, .62, .45]);
  box(architecture, [1.1, .6, .47], mats.wood, [1.5, .8, -1.45]);
  box(architecture, [1.15, .05, .53], mats.wall, [1.5, 1.13, -1.45]);
  box(architecture, [1.75, .25, 1.06], mats.wood, [-.66, 2.12, -.82]);
  box(architecture, [1.62, .16, .98], mats.upholstery, [-.66, 2.31, -.82]);
  box(architecture, [.38, .09, .7], mats.wall, [-1.22, 2.42, -.82]);
  box(architecture, [.95, .06, .5], mats.wood, [1.35, 2.53, -1.32]);
  box(architecture, [.1, .55, .45], mats.dark, [1.7, 2.22, -1.32]);
  for (const y of [1.55, 3]) sphere(architecture, .115, mats.lamp, [.25, y, -.35]);
  for (const y of [1.71, 3.13]) box(y > 2 ? houseShell : architecture, [4, .025, .025], mats.lamp, [0, y, -1.82]);
  for (let i = 0; i < 5; i++) box(architecture, [.08, .85, .09], mats.edge, [.3 + i * .44, .84, 1.75]);
  box(architecture, [1.95, .06, .06], mats.edge, [1.18, 1.27, 1.75]);
  box(architecture, [1.4, .12, .55], mats.concrete, [-.6, .27, 2.05]);

  for (let row = 0; row < 2; row++) for (let col = 0; col < 4; col++) {
    const z = -.45 + row * 1.36;
    const y = 3.55 - z * .141;
    const x = -1.68 + col * 1.1;
    box(devices.pv, [1.04, .04, 1.24], mats.panelFrame, [x, y, z], [.14, 0, 0]);
    box(devices.pv, [.96, .025, 1.15], mats.panel, [x, y + .035, z], [.14, 0, 0]);
    for (let j = 0; j < 3; j++) box(devices.pv, [.012, .008, 1.15], mats.panelFrame, [x - .24 + j * .24, y + .053, z], [.14, 0, 0]);
  }

  box(devices.battery, [.72, 1.68, .64], mats.shell, [-3.15, 1.09, .6]);
  box(devices.battery, [.59, 1.44, .04], mats.dark, [-3.15, 1.1, .94]);
  box(devices.battery, [.06, 1.32, .05], mats.edge, [-3.35, 1.1, .97]);
  box(devices.battery, [.77, .09, .69], mats.edge, [-3.15, .29, .6]);
  const batteryFill = box(root, [.07, 1.28, .055], mats.teal, [-3.35, 1.1, .985]);
  batteryFill.visible = false;
  batteryFill.userData.device = 'battery';

  box(devices.grid, [.62, 1.18, .57], mats.shell, [3.25, .89, 1.13]);
  box(devices.grid, [.44, .38, .035], mats.dark, [3.25, 1.12, 1.44]);
  cylinder(devices.grid, [.048, .048], 1.75, mats.edge, [3.27, 2.32, 1.13], 8);
  box(devices.grid, [.75, .08, .09], mats.edge, [3.27, 2.89, 1.13]);

  const tree = (x, z, scale) => {
    cylinder(architecture, [.075 * scale, .12 * scale], 1.05 * scale, mats.wood, [x, .6 * scale, z], 7);
    sphere(architecture, .47 * scale, mats.green, [x, 1.12 * scale, z], [1, 1.15, 1]);
    sphere(architecture, .38 * scale, mats.leaf, [x - .15 * scale, 1.43 * scale, z], [1, 1.1, 1]);
  };
  tree(-3.3, -1.8, 1.28); tree(3.15, -2.13, 1.22); tree(3.9, -.8, .72); tree(-3.8, -.34, .6);
  for (const [x, z] of [[-2.7, 2.26], [2.58, -1.4], [-1.7, -2.4], [2.5, 2.4]]) {
    cylinder(architecture, [.17, .14], .25, mats.rust, [x, .36, z], 8);
    sphere(architecture, .25, mats.leaf, [x, .62, z]);
  }
  for (const x of [-2.35, 2.5]) {
    cylinder(architecture, [.04, .04], .52, mats.dark, [x, .48, 2.48], 8);
    sphere(architecture, .095, mats.lamp, [x, .78, 2.48]);
  }

  // A decorative partner character, with no invented speech or device readings.
  sphere(architecture, .29, mats.shell, [-2.03, .76, 2.17], [.86, 1, .72]);
  sphere(architecture, .37, mats.shell, [-2.03, 1.2, 2.17], [1, .82, .82]);
  sphere(architecture, .3, mats.dark, [-2.03, 1.22, 2.36], [1, .64, .5]);
  for (const x of [-2.14, -1.94]) sphere(architecture, .045, mats.eye, [x, 1.24, 2.51], [1, 1.2, .6]);
  for (const x of [-2.23, -1.83]) sphere(architecture, .11, mats.shell, [x, .49, 2.17], [1, .7, 1.4]);
  for (const x of [-2.36, -1.7]) sphere(architecture, .085, mats.shell, [x, .79, 2.17], [.8, 1.4, .8]);

  batch(architecture);
  batch(houseShell);
  Object.values(devices).forEach(batch);
  return { root, devices, batteryFill, houseShell };
}

const routePoints = {
  pv: [[-.7, 3.62, -.3], [-2.55, 3.62, -.3], [-2.72, .36, -.3], [-2.72, .36, 2.8], [0, .36, 2.8]],
  load: [[0, .36, 2.8], [0, .36, 1.18]],
  battery: [[-3.15, .36, 1.06], [-3.15, .36, 2.8], [0, .36, 2.8]],
  gridImport: [[3.25, .36, 1.46], [3.25, .36, 2.8], [0, .36, 2.8]],
  gridExport: [[3.25, .39, 1.46], [3.25, .39, 2.85], [0, .39, 2.85]],
};
const routeColors = { pv: '#ffcd76', load: '#f2d9b2', battery: '#68e9d1', gridImport: '#79bdff', gridExport: '#c3a5ff' };

function disposeObject(object) {
  const geometries = new Set(); const materials = new Set(); const textures = new Set();
  object.traverse(part => {
    if (part.geometry) geometries.add(part.geometry);
    if (part.material) (Array.isArray(part.material) ? part.material : [part.material]).forEach(mat => {
      materials.add(mat);
      Object.values(mat).forEach(value => { if (value?.isTexture) textures.add(value); });
    });
  });
  geometries.forEach(geometry => geometry.dispose());
  materials.forEach(mat => mat.dispose());
  textures.forEach(texture => { texture.source?.data?.close?.(); texture.dispose(); });
}

export function fitPanelsToGeneratedRoof(house, panels) {
  house.updateMatrixWorld(true);
  panels.updateMatrixWorld(true);
  const down = new THREE.Raycaster();
  const roof = z => {
    down.set(new THREE.Vector3(0, 6, z), new THREE.Vector3(0, -1, 0));
    return down.intersectObject(house, true)[0]?.point ?? null;
  };
  // This verified asset has an x-axis ridge; use its front slope with eave margins.
  const back = roof(-.22), front = roof(.95);
  if (!back || !front) return null;
  const angle = Math.atan2(back.y - front.y, front.z - back.z);
  if (angle < .2 || angle > .9) return null;
  const center = new THREE.Box3().setFromObject(panels).getCenter(new THREE.Vector3());
  const flatten = new THREE.Matrix4().makeRotationX(-.14).multiply(new THREE.Matrix4().makeTranslation(-center.x, -center.y, -center.z));
  const flatBounds = new THREE.Box3();
  const vertex = new THREE.Vector3();
  panels.traverse(part => {
    const positions = part.geometry?.getAttribute('position');
    if (!positions) return;
    for (let i = 0; i < positions.count; i++) flatBounds.expandByPoint(vertex.fromBufferAttribute(positions, i).applyMatrix4(part.matrixWorld).applyMatrix4(flatten));
  });
  const size = flatBounds.getSize(new THREE.Vector3());
  const target = back.clone().add(front).multiplyScalar(.5).add(new THREE.Vector3(0, Math.cos(angle), Math.sin(angle)).multiplyScalar(.055));
  panels.matrix.copy(new THREE.Matrix4().makeTranslation(...target.toArray())
    .multiply(new THREE.Matrix4().makeRotationX(angle))
    .multiply(new THREE.Matrix4().makeScale(4 / size.x, 1, back.distanceTo(front) / size.z))
    .multiply(flatten));
  panels.matrixAutoUpdate = false;
  panels.updateMatrixWorld(true);
  return { center: target, angle };
}

export function createWorld(container, onSelect, onFailure, onAssetState) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' });
  let scene;
  let cleanup = () => {};
  try {
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.35;
  scene = new THREE.Scene();
  const island = createEnergyIsland();
  scene.add(island.root);
  scene.add(new THREE.HemisphereLight('#aecfff', '#333658', 2.5));
  const sun = new THREE.DirectionalLight('#ffe1b6', 3.5);
  sun.position.set(-3, 8, 7); scene.add(sun);
  const rim = new THREE.DirectionalLight('#74acff', 2.6);
  rim.position.set(4, 3, -5); scene.add(rim);
  const camera = new THREE.OrthographicCamera(-6, 6, 5, -5, .1, 80);
  camera.position.set(10, 7.8, 12);
  camera.zoom = 1.12;
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, .7, 0);
  controls.enablePan = false;
  controls.minPolarAngle = .7; controls.maxPolarAngle = 1.26;
  controls.minAzimuthAngle = -.35; controls.maxAzimuthAngle = 1.35;
  controls.minZoom = .82; controls.maxZoom = 1.5;
  controls.rotateSpeed = .55;
  controls.zoomSpeed = .65;
  controls.update(); controls.saveState();
  renderer.domElement.setAttribute('aria-hidden', 'true');
  renderer.domElement.tabIndex = -1;
  container.appendChild(renderer.domElement);

  const routes = Object.entries(routePoints).map(([key, points]) => {
    const curve = new THREE.CatmullRomCurve3(points.map(point => new THREE.Vector3(...point)), false, 'centripetal');
    const geometry = new THREE.BufferGeometry().setFromPoints(curve.getPoints(52));
    const lineMat = new THREE.LineDashedMaterial({ color: routeColors[key], dashSize: .12, gapSize: 0, transparent: true, opacity: .35 });
    const line = new THREE.Line(geometry, lineMat);
    line.computeLineDistances(); scene.add(line);
    const dotMat = new THREE.MeshBasicMaterial({ color: routeColors[key], transparent: true });
    const dots = [0, 1, 2].map(() => {
      const dot = new THREE.Mesh(new THREE.SphereGeometry(.045, 7, 5), dotMat);
      dot.visible = false; scene.add(dot); return dot;
    });
    return { key, curve, line, dots };
  });
  const junction = addPart(scene, new THREE.CylinderGeometry(.09, .09, .025, 16), new THREE.MeshBasicMaterial({ color: '#8be6e4' }), [0, .38, 2.8]);
  const halo = new THREE.Mesh(new THREE.TorusGeometry(.58, .018, 6, 40), new THREE.MeshBasicMaterial({ color: '#a0eee8', transparent: true, opacity: .9 }));
  halo.rotation.x = -Math.PI / 2;
  halo.visible = false; scene.add(halo);
  const anchors = { pv: [0, 3.53, .25], battery: [-3.15, .23, .6], grid: [3.25, .23, 1.13] };
  let pvRoofAngle = .14;
  const focuses = { pv: [0, 1.3, 0], battery: [-.75, .7, .4], grid: [.75, .7, .4] };
  let state = energyFlowState(null);
  let lastSelected = null;
  let reduced = false;
  let disposed = false;
  let visible = !document.hidden;
  let inView = true;
  let raf = 0;
  let renderCount = 0;
  let tween = null;
  let epoch = performance.now();
  const pointer = new THREE.Vector2();
  const raycaster = new THREE.Raycaster();
  let pointerDown = null;
  let sampleStart = null;
  let sampleFrames = 0;
  let reducedQuality = false;
  const stats = { frames: 0, triangles: 0, drawCalls: 0, pixelRatio: 1 };

  const canAnimate = () => state.moving && state.flows.some(flow => flow.power !== null && flow.power > 0);
  function render(now = performance.now()) {
    raf = 0;
    if (disposed || !visible || !inView) return;
    if (tween) {
      const t = Math.min(1, (now - tween.start) / 320);
      controls.target.lerpVectors(tween.from, tween.to, 1 - (1 - t) ** 3);
      controls.update();
      if (t === 1) tween = null;
    }
    for (const route of routes) {
      const flow = state.flows.find(item => item.key === route.key);
      const active = flow.power !== null && flow.power > 0;
      route.line.material.opacity = active ? state.preview ? .63 : .9 : .17;
      route.line.material.gapSize = state.preview ? .1 : 0;
      route.dots.forEach((dot, index) => {
        dot.visible = active && state.moving;
        if (!dot.visible) return;
        let t = ((now - epoch) * .00021 * Math.min(2, .75 + flow.power * .3) + index / 3) % 1;
        if (flow.reverse) t = 1 - t;
        dot.position.copy(route.curve.getPointAt(t));
        dot.material.opacity = state.preview ? .75 : 1;
      });
    }
    junction.material.color.set(state.preview ? '#b6f6ee' : '#8be6e4');
    renderer.render(scene, camera);
    renderCount++;
    Object.assign(stats, { frames: renderCount, triangles: renderer.info.render.triangles, drawCalls: renderer.info.render.calls, pixelRatio: renderer.getPixelRatio() });
    Object.assign(container.dataset, { ewFrames: String(stats.frames), ewTriangles: String(stats.triangles), ewDrawCalls: String(stats.drawCalls), ewDpr: String(stats.pixelRatio), ewMoving: String(canAnimate()), ewVisible: String(visible && inView), ewGeometries: String(renderer.info.memory.geometries), ewRenderer: 'three-native' });
    // shortcut: sample only while continuously animating; benchmark physical phones before release.
    if (canAnimate()) {
      sampleStart ??= now;
      sampleFrames++;
      if (sampleFrames === 90) {
        const fps = (sampleFrames - 1) * 1000 / Math.max(1, now - sampleStart);
        container.dataset.ewSampleFps = fps.toFixed(1);
        const target = matchMedia('(max-width: 720px)').matches ? 30 : 45;
        if (fps < target) {
          if (!reducedQuality) {
            reducedQuality = true; renderer.setPixelRatio(.8); resize();
          } else { onFailure('performance'); return; }
        }
        sampleStart = null; sampleFrames = 0;
      }
    } else { sampleStart = null; sampleFrames = 0; }
    if (canAnimate() || tween) schedule();
  }
  function schedule() {
    if (!raf && !disposed && visible && inView) raf = requestAnimationFrame(render);
  }
  function resize() {
    const width = container.clientWidth;
    const height = container.clientHeight;
    if (!width || !height) return;
    const aspect = width / height;
    const viewHeight = aspect > 1.5 ? 7.3 : 10.6 / aspect;
    camera.left = -viewHeight * aspect / 2;
    camera.right = viewHeight * aspect / 2;
    camera.top = viewHeight / 2; camera.bottom = -viewHeight / 2;
    camera.updateProjectionMatrix();
    const cap = matchMedia('(max-width: 720px)').matches ? 1 : 1.5;
    renderer.setPixelRatio(reducedQuality ? .8 : Math.min(devicePixelRatio || 1, cap));
    renderer.setSize(width, height); schedule();
  }
  function pick(event) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects([...Object.values(island.devices), island.batteryFill], true)[0];
    let object = hit?.object;
    while (object && !object.userData.device) object = object.parent;
    return object?.userData.device ?? null;
  }
  const down = event => { pointerDown = [event.clientX, event.clientY]; };
  const up = event => {
    if (!pointerDown) return;
    const distance = Math.hypot(event.clientX - pointerDown[0], event.clientY - pointerDown[1]);
    pointerDown = null;
    if (distance <= 6) { const device = pick(event); if (device) onSelect(device); }
  };
  const move = event => { if (!pointerDown) renderer.domElement.style.cursor = pick(event) ? 'pointer' : 'grab'; };
  const cancel = () => { pointerDown = null; };
  const contextLost = event => { event.preventDefault(); onFailure('context'); };
  const visibility = () => {
    visible = !document.hidden;
    container.dataset.ewVisible = String(visible && inView);
    if (!visible) { cancelAnimationFrame(raf); raf = 0; sampleStart = null; sampleFrames = 0; }
    else { epoch = performance.now(); schedule(); }
  };
  renderer.domElement.addEventListener('pointerdown', down);
  renderer.domElement.addEventListener('pointerup', up);
  renderer.domElement.addEventListener('pointermove', move);
  renderer.domElement.addEventListener('pointercancel', cancel);
  renderer.domElement.addEventListener('webglcontextlost', contextLost);
  document.addEventListener('visibilitychange', visibility);
  controls.addEventListener('change', schedule);
  let observer;
  let intersection;
  cleanup = () => {
    disposed = true; cancelAnimationFrame(raf);
    observer?.disconnect(); intersection?.disconnect(); controls.dispose();
    document.removeEventListener('visibilitychange', visibility);
    renderer.domElement.removeEventListener('pointerdown', down);
    renderer.domElement.removeEventListener('pointerup', up);
    renderer.domElement.removeEventListener('pointermove', move);
    renderer.domElement.removeEventListener('pointercancel', cancel);
    renderer.domElement.removeEventListener('webglcontextlost', contextLost);
  };
  observer = new ResizeObserver(resize); observer.observe(container);
  intersection = new IntersectionObserver(entries => {
    inView = entries[0]?.isIntersecting ?? true;
    container.dataset.ewVisible = String(visible && inView);
    if (!inView) { cancelAnimationFrame(raf); raf = 0; sampleStart = null; sampleFrames = 0; }
    else schedule();
  });
  intersection.observe(container);
  resize();
  onAssetState('loading');
  new GLTFLoader().load('/energy-world/lux3d-house-4166575.glb', gltf => {
    if (disposed) { disposeObject(gltf.scene); return; }
    const bounds = new THREE.Box3().setFromObject(gltf.scene);
    const size = bounds.getSize(new THREE.Vector3());
    const center = bounds.getCenter(new THREE.Vector3());
    if (![size.x, size.y, size.z].every(value => Number.isFinite(value) && value > 0)) {
      disposeObject(gltf.scene); onAssetState('unavailable'); return;
    }
    gltf.scene.name = 'Aholo-Lux3D-house-task-4166575';
    // Illustration fitting, not installation dimensions or physical calibration.
    gltf.scene.scale.set(4.45 / size.x, 3.03 / size.y, 3.2 / size.z);
    gltf.scene.position.set(-center.x * gltf.scene.scale.x, .43 - bounds.min.y * gltf.scene.scale.y, -.42 - center.z * gltf.scene.scale.z);
    island.root.add(gltf.scene);
    const fit = fitPanelsToGeneratedRoof(gltf.scene, island.devices.pv);
    if (!fit) { island.root.remove(gltf.scene); disposeObject(gltf.scene); onAssetState('unavailable'); return; }
    anchors.pv = fit.center.toArray(); pvRoofAngle = fit.angle;
    if (lastSelected === 'pv') { halo.position.copy(fit.center); halo.rotation.x = -Math.PI / 2 + pvRoofAngle; }
    const pvRoute = routes.find(route => route.key === 'pv');
    pvRoute.curve.points[0].set(-.7, fit.center.y + .04, fit.center.z);
    pvRoute.curve.points[1].set(-2.55, fit.center.y + .04, fit.center.z);
    pvRoute.curve.updateArcLengths();
    pvRoute.line.geometry.dispose();
    pvRoute.line.geometry = new THREE.BufferGeometry().setFromPoints(pvRoute.curve.getPoints(52));
    pvRoute.line.computeLineDistances();
    island.houseShell.visible = false;
    container.dataset.ewHouseAsset = 'lux3d-4166575';
    onAssetState('lux3d'); schedule();
  }, undefined, () => {
    if (!disposed) { container.dataset.ewHouseAsset = 'procedural-fallback'; onAssetState('unavailable'); schedule(); }
  });

  return {
    update(frame, selected, reducedMotion) {
      reduced = reducedMotion;
      state = energyFlowState(frame, reduced);
      Object.assign(container.dataset, { ewFrameKind: frame?.kind ?? 'empty', ewFrameSlot: frame?.interval.slot == null ? 'unknown' : String(frame.interval.slot), ewSoc: state.soc === null ? 'unknown' : String(state.soc), ewBatteryKw: finite(frame?.batteryKw) ? String(frame.batteryKw) : 'unknown', ewSocFillVisible: String(state.soc !== null) });
      island.batteryFill.visible = state.soc !== null;
      if (state.soc !== null) {
        const height = state.soc / 100;
        island.batteryFill.scale.y = height;
        island.batteryFill.position.y = .46 + .64 * height;
      }
      halo.visible = selected !== null;
      if (selected) {
        halo.position.set(...anchors[selected]);
        halo.rotation.x = -Math.PI / 2 + (selected === 'pv' ? pvRoofAngle : 0);
        if (selected !== lastSelected) {
          const to = new THREE.Vector3(...focuses[selected]);
          if (reduced) { controls.target.copy(to); controls.update(); }
          else tween = { from: controls.target.clone(), to, start: performance.now() };
        }
      }
      lastSelected = selected;
      if (reduced && tween) { controls.target.copy(tween.to); tween = null; controls.update(); }
      schedule();
    },
    reset() { tween = null; controls.reset(); schedule(); },
    zoom(direction) { camera.zoom = THREE.MathUtils.clamp(camera.zoom * (direction > 0 ? 1.12 : 1 / 1.12), controls.minZoom, controls.maxZoom); camera.updateProjectionMatrix(); schedule(); },
    stats: () => ({ ...stats }),
    dispose() {
      if (disposed) return;
      cleanup();
      disposeObject(scene);
      renderer.dispose(); renderer.forceContextLoss(); renderer.domElement.remove();
      container.dataset.ewDisposed = 'true';
    },
  };
  } catch (error) {
    cleanup();
    if (scene) disposeObject(scene);
    renderer.dispose(); renderer.forceContextLoss(); renderer.domElement.remove();
    throw error;
  }
}
