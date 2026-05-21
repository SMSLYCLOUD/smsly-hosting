'use client';

import React, { useRef, useEffect, useMemo, useCallback, useState } from 'react';
import * as THREE from 'three';
import { TopologyGraph, TopologyNode, TopologyEdge } from '@/types/topology';
import { useGraphData } from '@/hooks/useGraphData';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { ServiceSidePanel } from './ServiceSidePanel';

// ─── Color Palette ────────────────────────────────────────
const COLORS = {
  platform: 0x0a0e1a,
  platformEdge: 0x1a2744,
  gridLine: 0x0f1a2e,
  serviceTower: 0x10b981,        // emerald for services
  addonCube: 0x8b5cf6,           // purple for addons
  volumeCube: 0xf59e0b,          // amber for volumes
  connectionBeam: 0x3b82f6,      // blue beams
  groundGlow: 0x10b981,
  ambientLight: 0x1a1a3e,
  fog: 0x05070f,
  text: '#e4e4e7',
  labelBg: 'rgba(0,0,0,0.75)',
};

// ─── Helpers ──────────────────────────────────────────────
function getTypeColor(type: string): number {
  const t = type?.toUpperCase();
  if (t === 'SERVICE') return COLORS.serviceTower;
  if (t === 'ADDON' || t === 'POSTGRES' || t === 'MYSQL' || t === 'REDIS' || t === 'MONGODB') return COLORS.addonCube;
  if (t === 'VOLUME') return COLORS.volumeCube;
  return COLORS.addonCube;
}

function getStatusEmissive(status: string): number {
  if (!status) return 0x333333;
  const s = status.toLowerCase();
  if (s === 'active' || s === 'running' || s === 'healthy') return 0x10b981;
  if (s === 'building' || s === 'deploying') return 0x3b82f6;
  if (s === 'failed' || s === 'error') return 0xef4444;
  if (s === 'queued') return 0xf59e0b;
  return 0x6b7280;
}

// ─── Create a text sprite label ──────────────────────────
function createLabel(text: string, position: THREE.Vector3, yOffset: number = 0.5): THREE.Sprite {
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d')!;
  canvas.width = 512;
  canvas.height = 128;

  // Background
  ctx.fillStyle = COLORS.labelBg;
  ctx.roundRect(8, 8, canvas.width - 16, canvas.height - 16, 12);
  ctx.fill();

  // Text
  ctx.fillStyle = COLORS.text;
  ctx.font = 'bold 36px Inter, system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  // Truncate
  let label = text;
  if (ctx.measureText(label).width > canvas.width - 48) {
    while (ctx.measureText(label + '…').width > canvas.width - 48 && label.length > 0) {
      label = label.slice(0, -1);
    }
    label += '…';
  }
  ctx.fillText(label, canvas.width / 2, canvas.height / 2);

  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false });
  const sprite = new THREE.Sprite(material);
  sprite.position.copy(position);
  sprite.position.y += yOffset;
  sprite.scale.set(3, 0.75, 1);
  return sprite;
}

// ─── Create a service tower ────────────────────────────
function createServiceTower(
  node: TopologyNode,
  x: number,
  z: number,
  height: number
): THREE.Group {
  const group = new THREE.Group();
  const baseColor = getTypeColor(node.type);
  const statusColor = getStatusEmissive(node.data?.status || '');

  // Main tower body
  const geometry = new THREE.BoxGeometry(1.6, height, 1.6);
  const material = new THREE.MeshPhysicalMaterial({
    color: baseColor,
    emissive: statusColor,
    emissiveIntensity: 0.3,
    metalness: 0.7,
    roughness: 0.2,
    transparent: true,
    opacity: 0.85,
    clearcoat: 1.0,
    clearcoatRoughness: 0.1,
  });
  const tower = new THREE.Mesh(geometry, material);
  tower.position.set(x, height / 2, z);
  tower.castShadow = true;
  tower.receiveShadow = true;
  (tower as any).userData = { nodeId: node.id, type: node.type?.toUpperCase() };
  group.add(tower);

  // Tower wireframe overlay
  const wireGeo = new THREE.BoxGeometry(1.62, height + 0.02, 1.62);
  const wireMat = new THREE.MeshBasicMaterial({
    color: baseColor,
    wireframe: true,
    transparent: true,
    opacity: 0.15,
  });
  const wireframe = new THREE.Mesh(wireGeo, wireMat);
  wireframe.position.copy(tower.position);
  group.add(wireframe);

  // Rooftop glow
  const roofLightGeo = new THREE.PlaneGeometry(1.2, 1.2);
  const roofLightMat = new THREE.MeshBasicMaterial({
    color: statusColor,
    transparent: true,
    opacity: 0.6,
    side: THREE.DoubleSide,
  });
  const roofLight = new THREE.Mesh(roofLightGeo, roofLightMat);
  roofLight.rotation.x = -Math.PI / 2;
  roofLight.position.set(x, height + 0.02, z);
  group.add(roofLight);

  // Point light on top
  const topLight = new THREE.PointLight(statusColor, 2, 8);
  topLight.position.set(x, height + 0.5, z);
  group.add(topLight);

  // Ground glow ring
  const ringGeo = new THREE.RingGeometry(1.2, 1.8, 32);
  const ringMat = new THREE.MeshBasicMaterial({
    color: baseColor,
    transparent: true,
    opacity: 0.2,
    side: THREE.DoubleSide,
  });
  const ring = new THREE.Mesh(ringGeo, ringMat);
  ring.rotation.x = -Math.PI / 2;
  ring.position.set(x, 0.02, z);
  group.add(ring);

  // Label
  const label = createLabel(
    node.data?.name || node.id,
    new THREE.Vector3(x, height + 0.8, z),
    0
  );
  group.add(label);

  return group;
}

// ─── Create addon cube ───────────────────────────────
function createAddonCube(
  node: TopologyNode,
  x: number,
  z: number,
): THREE.Group {
  const group = new THREE.Group();
  const baseColor = getTypeColor(node.type);
  const height = 0.8;

  // Smaller cube
  const geometry = new THREE.BoxGeometry(0.8, height, 0.8);
  const material = new THREE.MeshPhysicalMaterial({
    color: baseColor,
    emissive: baseColor,
    emissiveIntensity: 0.2,
    metalness: 0.8,
    roughness: 0.15,
    transparent: true,
    opacity: 0.9,
  });
  const cube = new THREE.Mesh(geometry, material);
  cube.position.set(x, height / 2, z);
  cube.castShadow = true;
  (cube as any).userData = { nodeId: node.id, type: 'ADDON' };
  group.add(cube);

  // Label
  const label = createLabel(
    node.data?.name || node.id,
    new THREE.Vector3(x, height + 0.6, z),
    0
  );
  label.scale.set(2, 0.5, 1);
  group.add(label);

  return group;
}

// ─── Create glowing bridge between two points ────────
function createBridge(
  from: THREE.Vector3,
  to: THREE.Vector3,
  edgeType: string
): THREE.Group {
  const group = new THREE.Group();
  const mid = new THREE.Vector3().lerpVectors(from, to, 0.5);
  mid.y = Math.max(from.y, to.y) * 0.8 + 1.5;

  // Curved beam via quadratic bezier
  const curve = new THREE.QuadraticBezierCurve3(from, mid, to);
  const points = curve.getPoints(32);
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  
  const color = edgeType === 'STORAGE' ? 0xf59e0b : COLORS.connectionBeam;
  const material = new THREE.LineBasicMaterial({
    color,
    transparent: true,
    opacity: 0.6,
    linewidth: 2,
  });
  const line = new THREE.Line(geometry, material);
  group.add(line);

  // Tube for glow effect
  const tubeGeo = new THREE.TubeGeometry(curve, 32, 0.04, 8, false);
  const tubeMat = new THREE.MeshBasicMaterial({
    color,
    transparent: true,
    opacity: 0.3,
  });
  const tube = new THREE.Mesh(tubeGeo, tubeMat);
  group.add(tube);

  return group;
}

// ─── Ground grid ────────────────────────────────────
function createGround(): THREE.Group {
  const group = new THREE.Group();

  // Platform
  const platformGeo = new THREE.BoxGeometry(60, 0.15, 60);
  const platformMat = new THREE.MeshPhysicalMaterial({
    color: COLORS.platform,
    metalness: 0.9,
    roughness: 0.5,
    transparent: true,
    opacity: 0.95,
  });
  const platform = new THREE.Mesh(platformGeo, platformMat);
  platform.position.y = -0.075;
  platform.receiveShadow = true;
  group.add(platform);

  // Grid lines
  const gridHelper = new THREE.GridHelper(60, 40, COLORS.gridLine, COLORS.gridLine);
  (gridHelper.material as THREE.Material).transparent = true;
  (gridHelper.material as THREE.Material).opacity = 0.3;
  gridHelper.position.y = 0.01;
  group.add(gridHelper);

  return group;
}

// ═══════════════════════════════════════════════════
//  MAIN COMPONENT
// ═══════════════════════════════════════════════════
export function CityTopologyView() {
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const animFrameRef = useRef<number>(0);
  const { data, loading } = useGraphData();
  const [selectedNode, setSelectedNode] = useState<TopologyNode | null>(null);
  const raycaster = useRef(new THREE.Raycaster());
  const mouse = useRef(new THREE.Vector2());


  // ─── Layout algorithm ────────────────────
  const layout = useMemo(() => {
    if (!data) return { services: [], addons: [], bridges: [] };

    const serviceNodes = data.nodes.filter(n => n.type?.toUpperCase() === 'SERVICE');
    const addonNodes = data.nodes.filter(n => n.type?.toUpperCase() !== 'SERVICE');

    // Position services in a row with spacing
    const spacing = 6;
    const totalWidth = (serviceNodes.length - 1) * spacing;
    const startX = -totalWidth / 2;

    const positions: Record<string, { x: number; z: number; height: number }> = {};

    const services = serviceNodes.map((node, i) => {
      const x = startX + i * spacing;
      const z = 0;
      // Height based on something meaningful — use name length hash as proxy
      const height = 2.5 + (node.data?.name?.length || 5) * 0.3;
      positions[node.id] = { x, z, height };
      return { node, x, z, height };
    });

    // Position addons near their owning service
    const addons: Array<{ node: TopologyNode; x: number; z: number }> = [];
    addonNodes.forEach((addon, idx) => {
      // Find which service owns this addon via edges
      const ownerEdge = data.edges.find(e => e.target === addon.id);
      const ownerPos = ownerEdge ? positions[ownerEdge.source] : null;

      if (ownerPos) {
        const angle = ((idx % 4) * Math.PI) / 2 + Math.PI / 4;
        const radius = 2.5;
        const ax = ownerPos.x + Math.cos(angle) * radius;
        const az = ownerPos.z + Math.sin(angle) * radius;
        positions[addon.id] = { x: ax, z: az, height: 0.8 };
        addons.push({ node: addon, x: ax, z: az });
      } else {
        // Float unattached addons to the side
        const ax = startX + services.length * spacing + 3;
        const az = idx * 2 - addonNodes.length;
        positions[addon.id] = { x: ax, z: az, height: 0.8 };
        addons.push({ node: addon, x: ax, z: az });
      }
    });

    // Bridges
    const bridges = data.edges
      .filter(e => positions[e.source] && positions[e.target])
      .map(e => {
        const s = positions[e.source];
        const t = positions[e.target];
        return {
          from: new THREE.Vector3(s.x, s.height * 0.6, s.z),
          to: new THREE.Vector3(t.x, t.height * 0.6, t.z),
          type: e.type,
        };
      });

    return { services, addons, bridges };
  }, [data]);

  // ─── Three.js setup ──────────────────────
  useEffect(() => {
    if (!containerRef.current || !data || data.nodes.length === 0) return;

    const container = containerRef.current;
    const w = container.clientWidth;
    const h = container.clientHeight;

    // Renderer
    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      powerPreference: 'high-performance',
    });
    renderer.setSize(w, h);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.2;
    container.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    // Scene
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(COLORS.fog);
    scene.fog = new THREE.FogExp2(COLORS.fog, 0.015);
    sceneRef.current = scene;

    // Camera
    const camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 200);
    camera.position.set(18, 14, 18);
    camera.lookAt(0, 0, 0);
    cameraRef.current = camera;

    // Controls
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.maxPolarAngle = Math.PI / 2 - 0.05;
    controls.minDistance = 5;
    controls.maxDistance = 60;
    controls.target.set(0, 2, 0);
    controlsRef.current = controls;

    // Lighting
    const ambientLight = new THREE.AmbientLight(COLORS.ambientLight, 0.8);
    scene.add(ambientLight);

    const hemiLight = new THREE.HemisphereLight(0x4040ff, 0x101020, 0.6);
    scene.add(hemiLight);

    const dirLight = new THREE.DirectionalLight(0xffffff, 1.0);
    dirLight.position.set(15, 20, 10);
    dirLight.castShadow = true;
    dirLight.shadow.mapSize.width = 2048;
    dirLight.shadow.mapSize.height = 2048;
    dirLight.shadow.camera.near = 0.5;
    dirLight.shadow.camera.far = 80;
    dirLight.shadow.camera.left = -30;
    dirLight.shadow.camera.right = 30;
    dirLight.shadow.camera.top = 30;
    dirLight.shadow.camera.bottom = -30;
    scene.add(dirLight);

    // Rim light
    const rimLight = new THREE.DirectionalLight(0x3b82f6, 0.4);
    rimLight.position.set(-10, 10, -10);
    scene.add(rimLight);

    // Ground
    scene.add(createGround());

    // Service towers
    layout.services.forEach(({ node, x, z, height }) => {
      const tower = createServiceTower(node, x, z, height);
      scene.add(tower);
    });

    // Addon cubes
    layout.addons.forEach(({ node, x, z }) => {
      const cube = createAddonCube(node, x, z);
      scene.add(cube);
    });

    // Bridges
    layout.bridges.forEach(({ from, to, type }) => {
      const bridge = createBridge(from, to, type);
      scene.add(bridge);
    });

    // Particles (ambient city dust)
    const particleCount = 200;
    const particleGeo = new THREE.BufferGeometry();
    const particlePositions = new Float32Array(particleCount * 3);
    for (let i = 0; i < particleCount; i++) {
      particlePositions[i * 3] = (Math.random() - 0.5) * 50;
      particlePositions[i * 3 + 1] = Math.random() * 15;
      particlePositions[i * 3 + 2] = (Math.random() - 0.5) * 50;
    }
    particleGeo.setAttribute('position', new THREE.BufferAttribute(particlePositions, 3));
    const particleMat = new THREE.PointsMaterial({
      color: 0x3b82f6,
      size: 0.05,
      transparent: true,
      opacity: 0.4,
    });
    const particles = new THREE.Points(particleGeo, particleMat);
    scene.add(particles);

    // Traffic System (Moving cars/swings)
    const trafficGroup = new THREE.Group();
    scene.add(trafficGroup);
    
    const trafficCount = layout.bridges.length * 3; // 3 cars per bridge
    const trafficCars: Array<{ mesh: THREE.Mesh; curve: THREE.QuadraticBezierCurve3; speed: number; offset: number }> = [];
    
    const carGeo = new THREE.SphereGeometry(0.06, 8, 8);
    layout.bridges.forEach((bridge, bIdx) => {
      // Recreate the curve for the traffic to follow
      const curve = new THREE.QuadraticBezierCurve3(bridge.from, 
        new THREE.Vector3().lerpVectors(bridge.from, bridge.to, 0.5).add(new THREE.Vector3(0, Math.max(bridge.from.y, bridge.to.y) * 0.8 + 1.5 - Math.max(bridge.from.y, bridge.to.y), 0)), 
        bridge.to
      );
      
      const color = bridge.type === 'STORAGE' ? 0xf59e0b : 0x60a5fa;
      
      for (let i = 0; i < 2; i++) {
        const carMat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.9 });
        const car = new THREE.Mesh(carGeo, carMat);
        const carLight = new THREE.PointLight(color, 1, 2);
        car.add(carLight);
        
        trafficGroup.add(car);
        trafficCars.push({
          mesh: car,
          curve,
          speed: 0.1 + Math.random() * 0.2,
          offset: Math.random()
        });
      }
    });

    // Animation loop
    let time = 0;
    const animate = () => {
      animFrameRef.current = requestAnimationFrame(animate);
      time += 0.005;
      controls.update();

      // Slowly rotate particles
      particles.rotation.y = time * 0.1;

      // Pulse tower roof lights
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh && obj.material instanceof THREE.MeshBasicMaterial) {
          if (obj.geometry instanceof THREE.PlaneGeometry) {
            obj.material.opacity = 0.3 + Math.sin(time * 4) * 0.3;
          }
        }
      });

      // Update traffic
      trafficCars.forEach(car => {
        car.offset += 0.002 * car.speed * 10;
        if (car.offset > 1) car.offset = 0;
        
        const pos = car.curve.getPoint(car.offset);
        car.mesh.position.copy(pos);
        
        // Fading at the ends
        const fade = Math.sin(car.offset * Math.PI);
        (car.mesh.material as THREE.MeshBasicMaterial).opacity = fade * 0.8;
      });

      renderer.render(scene, camera);
    };
    animate();

    // ─── Click detection ───────────────
    const handleClick = (event: MouseEvent) => {
      const rect = container.getBoundingClientRect();
      mouse.current.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.current.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.current.setFromCamera(mouse.current, camera);
      const intersects = raycaster.current.intersectObjects(scene.children, true);

      for (const hit of intersects) {
        let obj: THREE.Object3D | null = hit.object;
        while (obj) {
          if ((obj as any).userData?.nodeId) {
            const nodeId = (obj as any).userData.nodeId;
            const node = data?.nodes.find(n => n.id === nodeId);
            if (node) {
              setSelectedNode(node);
              return;
            }
          }
          obj = obj.parent;
        }
      }
      // Click on empty -> deselect
      setSelectedNode(null);
    };
    container.addEventListener('click', handleClick);

    // Resize handler
    const handleResize = () => {
      if (!container) return;
      const nw = container.clientWidth;
      const nh = container.clientHeight;
      camera.aspect = nw / nh;
      camera.updateProjectionMatrix();
      renderer.setSize(nw, nh);
    };
    window.addEventListener('resize', handleResize);

    return () => {
      cancelAnimationFrame(animFrameRef.current);
      container.removeEventListener('click', handleClick);
      window.removeEventListener('resize', handleResize);
      controls.dispose();
      renderer.dispose();
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [data, layout]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-emerald-500/30 border-t-emerald-400" />
          <span className="text-xs text-zinc-500">Loading city topology...</span>
        </div>
      </div>
    );
  }

  if (!data || data.nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-zinc-500 text-sm">
        No services to visualize. Deploy a service to see the city skyline.
      </div>
    );
  }

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
      
      {/* Legend */}
      <div className="absolute bottom-4 left-4 rounded-xl border border-zinc-800/60 bg-black/60 backdrop-blur-xl p-3 flex flex-col gap-2 text-[11px] text-zinc-400">
        <div className="flex items-center gap-2">
          <div className="h-3 w-3 rounded-sm" style={{ backgroundColor: '#10b981' }} />
          <span>Service</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="h-3 w-3 rounded-sm" style={{ backgroundColor: '#8b5cf6' }} />
          <span>Addon</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="h-3 w-3 rounded-sm" style={{ backgroundColor: '#f59e0b' }} />
          <span>Volume</span>
        </div>
        <hr className="border-zinc-700/50" />
        <div className="flex items-center gap-2">
          <div className="h-0.5 w-4 rounded-full" style={{ backgroundColor: '#3b82f6' }} />
          <span>Connection</span>
        </div>
      </div>

      {/* Controls hint */}
      <div className="absolute bottom-4 right-4 rounded-xl border border-zinc-800/60 bg-black/60 backdrop-blur-xl p-3 text-[10px] text-zinc-500 flex flex-col gap-1">
        <span>🖱️ Drag to orbit</span>
        <span>🔍 Scroll to zoom</span>
        <span>👆 Click tower for details</span>
      </div>

      {/* Side Panel */}
      {selectedNode && (
        <ServiceSidePanel
          node={selectedNode}
          onClose={() => setSelectedNode(null)}
        />
      )}
    </div>
  );
}

export default CityTopologyView;
