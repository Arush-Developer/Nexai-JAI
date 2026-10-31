// The Command Centre's 3D room, built entirely in code: five agent robots and the orchestrator that watches them.
// Nothing in this file holds data. setFocus says which agent is working, and the orchestrator turns to face it.
// The wall screens, globe and tablets are decoration, and deliberately show no numbers or places.
import * as THREE from "three";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { AGENT_IDS, AGENT_LOOK, type AgentId } from "./agents";

const ORCHESTRATOR_Z = -1.9;
const TURN_RATE = 5; // Larger is a faster turn. The orchestrator settles well within one focus step.
const PEDESTAL_HEIGHT = 0.55;
const ROBOT_SCALE = 1;

interface Robot {
  group: THREE.Group;
  head: THREE.Group;
  eyes: THREE.MeshStandardMaterial;
  accent: THREE.MeshStandardMaterial;
}

interface AgentRig {
  robot: Robot;
  ring: THREE.MeshStandardMaterial;
  light: THREE.PointLight;
  floorLink: THREE.MeshBasicMaterial;
  beam: THREE.MeshBasicMaterial;
  curve: THREE.QuadraticBezierCurve3;
  yaw: number;
  phase: number;
}

const white = () => new THREE.MeshPhysicalMaterial({ color: 0xcfd7e4, metalness: 0.2, roughness: 0.32, clearcoat: 1, clearcoatRoughness: 0.2 });
const darkMetal = (color = 0x10161f) => new THREE.MeshStandardMaterial({ color, metalness: 0.85, roughness: 0.42 });
const glow = (color: number, intensity: number) => new THREE.MeshStandardMaterial({ color: 0x000000, emissive: color, emissiveIntensity: intensity });

function mesh(geometry: THREE.BufferGeometry, material: THREE.Material, x = 0, y = 0, z = 0) {
  const m = new THREE.Mesh(geometry, material);
  m.position.set(x, y, z);
  return m;
}

function buildRobot(color: number, orchestrator = false): Robot {
  const group = new THREE.Group();
  const head = new THREE.Group();
  const shell = white();
  const eyes = glow(color, 3);
  const accent = glow(color, 1.4);
  const visorMaterial = new THREE.MeshStandardMaterial({ color: 0x04070e, metalness: 0.7, roughness: 0.16 });

  head.position.y = 1.32;
  head.add(mesh(new THREE.SphereGeometry(0.52, 48, 36), shell));
  const visor = mesh(new THREE.SphereGeometry(0.47, 40, 30), visorMaterial, 0, -0.02, 0.1);
  visor.scale.set(1.0, 0.82, 0.96);
  head.add(visor);
  for (const side of [-1, 1]) {
    const eye = mesh(new THREE.SphereGeometry(0.09, 20, 14), eyes, side * 0.17, 0.04, 0.55);
    eye.scale.set(orchestrator ? 1.25 : 1, orchestrator ? 0.65 : 1.05, 0.35);
    eye.rotation.z = orchestrator ? side * -0.25 : 0;
    head.add(eye);
    const ear = mesh(new THREE.CylinderGeometry(0.13, 0.13, 0.08, 28), shell, side * 0.52, 0, 0);
    ear.rotation.z = Math.PI / 2;
    head.add(ear);
    const earGlow = mesh(new THREE.TorusGeometry(0.085, 0.016, 10, 28), accent, side * 0.565, 0, 0);
    earGlow.rotation.y = Math.PI / 2;
    head.add(earGlow);
  }
  const smile = mesh(new THREE.TorusGeometry(0.09, 0.012, 8, 24, Math.PI * 0.8), eyes, 0, -0.1, 0.55);
  smile.rotation.z = Math.PI + Math.PI * 0.1;
  head.add(smile);
  const crown = mesh(new THREE.TorusGeometry(0.24, 0.012, 8, 40), accent, 0, 0.47, 0);
  crown.rotation.x = Math.PI / 2;
  head.add(crown);
  group.add(head);

  const torso = mesh(new THREE.SphereGeometry(0.36, 36, 28), shell, 0, 0.62, 0);
  torso.scale.set(0.95, 1.05, 0.82);
  group.add(torso);
  group.add(mesh(new THREE.CylinderGeometry(0.12, 0.15, 0.12, 20), darkMetal(), 0, 0.96, 0));
  group.add(mesh(new THREE.TorusGeometry(0.09, 0.016, 10, 28), accent, 0, 0.68, 0.3));
  group.add(mesh(new THREE.CircleGeometry(0.075, 24), eyes, 0, 0.68, 0.302));

  for (const side of [-1, 1]) {
    const shoulder = mesh(new THREE.SphereGeometry(0.095, 18, 14), darkMetal(0x2a3342), side * 0.36, 0.78, 0);
    group.add(shoulder);
    const arm = mesh(new THREE.CapsuleGeometry(0.07, 0.3, 8, 16), shell, side * 0.45, 0.56, 0.02);
    arm.rotation.z = side * 0.28;
    group.add(arm);
    group.add(mesh(new THREE.SphereGeometry(0.075, 16, 12), darkMetal(0x2a3342), side * 0.54, 0.34, 0.04));
    if (!orchestrator) {
      group.add(mesh(new THREE.CapsuleGeometry(0.085, 0.13, 8, 14), shell, side * 0.15, 0.2, 0));
      const foot = mesh(new THREE.SphereGeometry(0.13, 18, 12), darkMetal(0x2a3342), side * 0.16, 0.06, 0.05);
      foot.scale.set(1, 0.55, 1.35);
      group.add(foot);
    }
  }
  if (orchestrator) {
    const tail = mesh(new THREE.ConeGeometry(0.26, 0.52, 28), shell, 0, 0.14, 0);
    tail.rotation.x = Math.PI;
    group.add(tail);
  }
  return { group, head, eyes, accent };
}

/** An abstract glowing panel texture for the room's walls and the robots' tablets. It shows no readable data. */
function panelTexture(hue: number, seed: number) {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 160;
  const g = canvas.getContext("2d")!;
  let value = seed;
  const random = () => { value = (value * 16807) % 2147483647; return value / 2147483647; };
  g.fillStyle = `hsl(${hue} 70% 6%)`;
  g.fillRect(0, 0, 256, 160);
  g.strokeStyle = `hsla(${hue} 90% 60% / 0.14)`;
  for (let x = 0; x < 256; x += 22) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, 160); g.stroke(); }
  for (let y = 0; y < 160; y += 22) { g.beginPath(); g.moveTo(0, y); g.lineTo(256, y); g.stroke(); }
  for (let bar = 0; bar < 26; bar++) {
    const height = 12 + random() * 66;
    g.fillStyle = `hsla(${hue + random() * 40} 95% 62% / 0.75)`;
    g.fillRect(8 + bar * 9.4, 152 - height, 5, height);
  }
  g.strokeStyle = `hsla(${hue + 30} 100% 72% / 0.9)`;
  g.lineWidth = 2;
  g.beginPath();
  for (let x = 0; x <= 256; x += 16) g.lineTo(x, 34 + random() * 40);
  g.stroke();
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

export class AgentScene {
  private renderer: THREE.WebGLRenderer;
  private composer: EffectComposer;
  private bloom: UnrealBloomPass;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(29, 1, 0.1, 80);
  private rigs = {} as Record<AgentId, AgentRig>;
  private orchestrator: Robot;
  private halos: THREE.Mesh[] = [];
  private hologram: THREE.MeshBasicMaterial;
  private pulses: THREE.Mesh[] = [];
  private globe: THREE.Object3D;
  private focus: AgentId | null = null;
  private offline = new Set<AgentId>();
  private yawNow = 0;
  private yawTarget = 0;
  private frame = 0;
  private clock = new THREE.Clock();
  private visible = true;
  private observers: (ResizeObserver | IntersectionObserver)[] = [];
  private calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  private temp = new THREE.Vector3();
  private size = { width: 1, height: 1 };

  /** Runs after each drawn frame, so the page's labels can follow the robots. */
  onFrame: () => void = () => undefined;

  /** Throws if this browser cannot create a WebGL context. */
  constructor(canvas: HTMLCanvasElement, private container: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    const environment = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = environment.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = 0.42;
    environment.dispose();
    this.scene.background = new THREE.Color(0x03060c);
    this.scene.fog = new THREE.Fog(0x03060c, 14, 30);

    this.scene.add(new THREE.HemisphereLight(0x6f9dff, 0x05070c, 0.35));
    const key = new THREE.SpotLight(0xbcd4ff, 34, 30, 0.75, 0.7);
    key.position.set(0, 11, 5);
    key.target.position.set(0, 0, 0);
    this.scene.add(key, key.target);

    this.buildRoom();
    this.globe = this.buildGlobe();
    this.orchestrator = this.buildOrchestrator();
    this.hologram = this.buildPlinth();
    for (const id of AGENT_IDS) this.rigs[id] = this.buildAgent(id);
    for (let i = 0; i < 3; i++) {
      const pulse = mesh(new THREE.SphereGeometry(0.05, 12, 10), new THREE.MeshBasicMaterial({ color: 0xffffff }));
      pulse.visible = false;
      this.scene.add(pulse);
      this.pulses.push(pulse);
    }

    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.42, 0.5, 0.9);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());

    const resize = new ResizeObserver(() => this.resize());
    resize.observe(container);
    const seen = new IntersectionObserver(([entry]) => { this.visible = entry.isIntersecting; });
    seen.observe(container);
    this.observers.push(resize, seen);
    this.resize();
    this.tick();
  }

  private buildRoom() {
    const wallMaterial = new THREE.MeshStandardMaterial({ color: 0x070c15, metalness: 0.6, roughness: 0.6 });
    const back = mesh(new THREE.PlaneGeometry(40, 14), wallMaterial, 0, 6, -9);
    this.scene.add(back);
    const floor = mesh(new THREE.CircleGeometry(19, 72), new THREE.MeshStandardMaterial({ color: 0x080d15, metalness: 0.92, roughness: 0.34 }));
    floor.rotation.x = -Math.PI / 2;
    this.scene.add(floor);

    for (const [radius, opacity] of [[5.3, 0.6], [5.8, 0.25], [6.6, 0.4]] as const) {
      const ring = mesh(new THREE.TorusGeometry(radius, 0.018, 8, 128), new THREE.MeshBasicMaterial({ color: 0x2f8bff, transparent: true, opacity }), 0, 0.02, 0);
      ring.rotation.x = Math.PI / 2;
      this.scene.add(ring);
    }
    // The warm light arcs around the stage's front edge.
    for (const [start, length] of [[0.3, 0.75], [2.1, 0.75]] as const) {
      const arc = mesh(new THREE.TorusGeometry(6.1, 0.05, 8, 64, length), glow(0xffa64d, 1.2), 0, 0.09, 0);
      arc.rotation.x = Math.PI / 2;
      arc.rotation.z = start;
      this.scene.add(arc);
    }
    // Rows of wall screens, with hues that echo the agents.
    const hues = [212, 200, 330, 212, 190, 0, 212, 275, 205, 170];
    hues.forEach((hue, i) => {
      const texture = panelTexture(hue, 4099 + i * 977);
      const material = new THREE.MeshStandardMaterial({ map: texture, emissiveMap: texture, emissive: 0xffffff, emissiveIntensity: 0.62, color: 0x111111 });
      const left = i < hues.length / 2;
      const column = left ? i : i - hues.length / 2;
      const screen = mesh(new THREE.PlaneGeometry(2.0, 1.25), material, (left ? -1 : 1) * (2.6 + column * 1.45), 1.7 + (column % 2) * 1.4, -8.3 + column * 0.75);
      screen.rotation.y = (left ? 1 : -1) * 0.42;
      this.scene.add(screen);
    });
    const hero = panelTexture(320, 90001);
    const big = mesh(new THREE.PlaneGeometry(3.8, 2.4), new THREE.MeshStandardMaterial({ map: hero, emissiveMap: hero, emissive: 0xffffff, emissiveIntensity: 0.7, color: 0x111111 }), -7.9, 2.5, -6.2);
    big.rotation.y = 0.72;
    this.scene.add(big);
    // Ceiling light strips, converging over the stage.
    const stripMaterial = glow(0xffe2bd, 2.4);
    for (let i = 0; i < 16; i++) {
      const side = i % 2 ? 1 : -1;
      const row = Math.floor(i / 2);
      const strip = mesh(new THREE.BoxGeometry(1.15, 0.05, 0.16), stripMaterial, side * (1.2 + row * 1.2), 4.6 - row * 0.02, -8.2 + row * 0.5);
      strip.rotation.y = side * -0.55;
      this.scene.add(strip);
    }
    for (const x of [-6.4, -3.4, 3.4, 6.4]) {
      this.scene.add(mesh(new THREE.BoxGeometry(0.05, 5, 0.05), glow(0x2f8bff, 1.6), x, 2.5, -8.6));
    }
  }

  private buildGlobe() {
    const globe = new THREE.Group();
    globe.position.set(7.2, 2.7, -6.4);
    globe.add(mesh(new THREE.SphereGeometry(1.2, 40, 30), new THREE.MeshStandardMaterial({ color: 0x06264f, emissive: 0x0d4d9e, emissiveIntensity: 0.65, roughness: 0.6 })));
    const wire = mesh(new THREE.SphereGeometry(1.22, 24, 16), new THREE.MeshBasicMaterial({ color: 0x4da3ff, wireframe: true, transparent: true, opacity: 0.32 }));
    globe.add(wire);
    this.scene.add(globe);
    return globe;
  }

  private buildOrchestrator() {
    const robot = buildRobot(0x35a7ff, true);
    robot.group.scale.setScalar(1.3);
    robot.group.position.set(0, 1.95, ORCHESTRATOR_Z);
    this.scene.add(robot.group);
    for (const [radius, tiltX, tiltZ] of [[0.86, 1.28, 0.2], [0.98, 1.52, -0.22]] as const) {
      const halo = mesh(new THREE.TorusGeometry(radius, 0.018, 10, 96), glow(0x35a7ff, 2.4), 0, 0.72, 0);
      halo.rotation.set(tiltX, 0, tiltZ);
      robot.group.add(halo);
      this.halos.push(halo);
    }
    return robot;
  }

  /** The stepped plinth and hologram cone under the orchestrator. Returns the cone's material. */
  private buildPlinth() {
    const z = ORCHESTRATOR_Z;
    const blocks: [number, number, number][] = [[2.1, 2.35, 0.22], [1.6, 1.8, 0.2], [1.05, 1.22, 0.16]];
    let y = 0;
    for (const [top, bottom, height] of blocks) {
      this.scene.add(mesh(new THREE.CylinderGeometry(top, bottom, height, 72), darkMetal(0x151c27), 0, y + height / 2, z));
      y += height;
      const ring = mesh(new THREE.TorusGeometry(top - 0.14, 0.022, 10, 96), glow(0x35a7ff, 2.6), 0, y + 0.005, z);
      ring.rotation.x = Math.PI / 2;
      this.scene.add(ring);
    }
    const material = new THREE.MeshBasicMaterial({ color: 0x3fb0ff, transparent: true, opacity: 0.2, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, depthWrite: false });
    const cone = mesh(new THREE.ConeGeometry(0.72, 1.45, 48, 1, true), material, 0, y + 0.72, z);
    cone.rotation.x = Math.PI;
    this.scene.add(cone);
    return material;
  }

  private buildAgent(id: AgentId): AgentRig {
    const look = AGENT_LOOK[id];
    const [x, z] = look.position;
    const base = new THREE.Group();
    base.position.set(x, 0, z);
    this.scene.add(base);

    base.add(mesh(new THREE.BoxGeometry(2.0, PEDESTAL_HEIGHT * 0.62, 1.7), darkMetal(0x161d28), 0, PEDESTAL_HEIGHT * 0.31, 0));
    base.add(mesh(new THREE.CylinderGeometry(0.82, 0.92, PEDESTAL_HEIGHT * 0.38, 56), darkMetal(0x1f2835), 0, PEDESTAL_HEIGHT * 0.81, 0));
    const ringMaterial = glow(look.color, 1.6);
    const ring = mesh(new THREE.TorusGeometry(0.72, 0.03, 10, 72), ringMaterial, 0, PEDESTAL_HEIGHT + 0.005, 0);
    ring.rotation.x = Math.PI / 2;
    base.add(ring);
    const front = mesh(new THREE.BoxGeometry(1.5, 0.028, 0.02), ringMaterial, 0, PEDESTAL_HEIGHT * 0.3, 0.86);
    base.add(front);

    const robot = buildRobot(look.color);
    robot.group.scale.setScalar(ROBOT_SCALE);
    robot.group.position.y = PEDESTAL_HEIGHT;
    const yaw = Math.atan2(-x, 9) * 0.7;
    robot.group.rotation.y = yaw;
    base.add(robot.group);

    const tabletTexture = panelTexture(new THREE.Color(look.color).getHSL({ h: 0, s: 0, l: 0 }).h * 360, 7001 + look.color % 997);
    const tablet = mesh(new THREE.PlaneGeometry(0.5, 0.34), new THREE.MeshStandardMaterial({ map: tabletTexture, emissiveMap: tabletTexture, emissive: 0xffffff, emissiveIntensity: 1, side: THREE.DoubleSide, transparent: true, opacity: 0.9 }), x < 0 ? 0.74 : -0.74, PEDESTAL_HEIGHT + 0.72, 0.28);
    tablet.rotation.y = x < 0 ? -0.5 : 0.5;
    base.add(tablet);

    const light = new THREE.PointLight(look.color, 1.2, 5, 1.7);
    light.position.set(0, PEDESTAL_HEIGHT + 0.5, 0.9);
    base.add(light);

    const from = new THREE.Vector3(0, 3.0, ORCHESTRATOR_Z);
    const to = new THREE.Vector3(x, PEDESTAL_HEIGHT + 0.72, z);
    const curve = new THREE.QuadraticBezierCurve3(from, new THREE.Vector3(x * 0.55, 3.4, (z + ORCHESTRATOR_Z) / 2), to);
    const beam = new THREE.MeshBasicMaterial({ color: look.color, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false });
    this.scene.add(mesh(new THREE.TubeGeometry(curve, 48, 0.024, 8), beam));

    const floorLink = new THREE.MeshBasicMaterial({ color: look.color, transparent: true, opacity: 0.25, blending: THREE.AdditiveBlending, depthWrite: false });
    const floorCurve = new THREE.LineCurve3(new THREE.Vector3(0, 0.03, ORCHESTRATOR_Z), new THREE.Vector3(x, 0.03, z));
    this.scene.add(mesh(new THREE.TubeGeometry(floorCurve, 8, 0.02, 6), floorLink));

    return { robot, ring: ringMaterial, light, floorLink, beam, curve, yaw, phase: x + z };
  }

  /** The agent the orchestrator should watch, or null when nothing is working. */
  setFocus(id: AgentId | null) {
    this.focus = id;
    if (id) {
      const [x, z] = AGENT_LOOK[id].position;
      this.yawTarget = Math.atan2(x, z - ORCHESTRATOR_Z);
    } else {
      this.yawTarget = 0;
    }
    this.container.dataset.focus = id ?? "none";
    this.container.dataset.yawTarget = this.yawTarget.toFixed(3);
  }

  /** Agents that cannot run on this input, such as Agents 4 and 5 without endpoints. */
  setOffline(ids: AgentId[]) {
    this.offline = new Set(ids);
  }

  /** The agent's position on screen, in pixels from the container's top left corner. */
  project(id: AgentId, side: number, height: number) {
    const [x, z] = AGENT_LOOK[id].position;
    this.temp.set(x + side, height, z).project(this.camera);
    return { x: (this.temp.x * 0.5 + 0.5) * this.size.width, y: (-this.temp.y * 0.5 + 0.5) * this.size.height };
  }

  private resize() {
    const { width, height } = this.container.getBoundingClientRect();
    if (!width || !height) return;
    this.size = { width, height };
    this.renderer.setSize(width, height, false);
    this.composer.setSize(width, height);
    this.bloom.setSize(width, height);
    this.camera.aspect = width / height;
    // Pull back on narrow screens, so the outer robots stay in frame.
    const back = Math.max(1, 1.72 / this.camera.aspect);
    this.camera.position.set(0, 6.4 * back, 11.2 * back);
    this.camera.lookAt(0, 1.75, 0.2);
    // Fog follows the camera back, or a far camera on a phone would see nothing but fog.
    const fog = this.scene.fog as THREE.Fog;
    fog.near = 14 * back;
    fog.far = 30 * back;
    this.camera.updateProjectionMatrix();
  }

  private tick = () => {
    this.frame = requestAnimationFrame(this.tick);
    if (!this.visible) { this.clock.getDelta(); return; }
    const dt = Math.min(this.clock.getDelta(), 0.1);
    const time = this.clock.elapsedTime;
    const motion = this.calm ? 0 : 1;

    // The turn is framerate independent, so a slow laptop still arrives on time.
    const blend = this.calm ? 1 : 1 - Math.exp(-TURN_RATE * dt);
    this.yawNow += (this.yawTarget - this.yawNow) * blend;
    const o = this.orchestrator;
    o.group.rotation.y = this.yawNow;
    o.group.position.y = 1.95 + Math.sin(time * 1.3) * 0.045 * motion;
    o.head.rotation.x = this.focus ? 0.24 : 0;
    this.halos.forEach((halo, i) => { halo.rotation.z += dt * (i ? -0.5 : 0.7) * motion; });
    o.eyes.emissiveIntensity = this.focus ? 4.2 : 2.2;
    this.hologram.opacity = (this.focus ? 0.3 : 0.15) + Math.sin(time * 2.2) * 0.04 * motion;
    this.globe.rotation.y += dt * 0.08 * motion;

    for (const id of AGENT_IDS) {
      const rig = this.rigs[id];
      const active = this.focus === id;
      const off = this.offline.has(id);
      const ease = 1 - Math.exp(-8 * dt);
      const approach = (now: number, want: number) => now + (want - now) * (this.calm ? 1 : ease);
      rig.robot.eyes.emissiveIntensity = approach(rig.robot.eyes.emissiveIntensity, off ? 0.12 : active ? 6 : 1.7);
      rig.robot.accent.emissiveIntensity = approach(rig.robot.accent.emissiveIntensity, off ? 0.1 : active ? 3 : 1);
      rig.ring.emissiveIntensity = approach(rig.ring.emissiveIntensity, off ? 0.08 : active ? 5 : 1.2);
      rig.light.intensity = approach(rig.light.intensity, off ? 0 : active ? 5 : 1.2);
      rig.beam.opacity = approach(rig.beam.opacity, active ? 0.62 : 0);
      rig.floorLink.opacity = approach(rig.floorLink.opacity, off ? 0.03 : active ? 0.95 : 0.22);
      rig.robot.group.position.y = PEDESTAL_HEIGHT + (active ? Math.abs(Math.sin(time * 5)) * 0.07 * motion : Math.sin(time * 1.1 + rig.phase) * 0.012 * motion);
      rig.robot.head.rotation.y = active ? Math.sin(time * 3) * 0.2 * motion : 0;
    }

    const curve = this.focus ? this.rigs[this.focus].curve : null;
    this.pulses.forEach((pulse, i) => {
      pulse.visible = !!curve && motion === 1;
      if (curve) pulse.position.copy(curve.getPoint(((time * 0.9 + i / 3) % 1)));
    });

    this.composer.render();
    this.container.dataset.yaw = this.yawNow.toFixed(3);
    this.onFrame();
  };

  dispose() {
    cancelAnimationFrame(this.frame);
    this.observers.forEach((observer) => observer.disconnect());
    this.scene.traverse((object) => {
      const item = object as THREE.Mesh;
      item.geometry?.dispose();
      const materials = Array.isArray(item.material) ? item.material : item.material ? [item.material] : [];
      for (const material of materials) {
        (material as THREE.MeshStandardMaterial).map?.dispose();
        material.dispose();
      }
    });
    this.scene.environment?.dispose();
    this.composer.dispose();
    this.renderer.dispose();
  }
}
