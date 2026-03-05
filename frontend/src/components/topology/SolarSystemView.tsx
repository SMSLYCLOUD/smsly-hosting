'use client';

import React, { useEffect, useRef, useState, useMemo } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { useGraphData } from '@/hooks/useGraphData';
import { TopologyNode, TopologyEdge } from '@/types/topology';
import { Loader2, ArrowLeft, ArrowRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ServiceSidePanel } from './ServiceSidePanel';
import { ErrorBoundary } from '@/components/ErrorBoundary';

function SolarSystemContent() {
  const { data, loading, error } = useGraphData();
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const systemsRef = useRef<any[]>([]); // Store system data for animation
  const frameIdRef = useRef<number>(0);

  // Group services with their addons
  const systems = useMemo(() => {
    if (!data) return [];

    const normalizeNodeType = (value: unknown) => String(value || '').toLowerCase();
    const serviceNodes = data.nodes.filter(n => normalizeNodeType(n.type) === 'service');
    const addonNodes = data.nodes.filter(n => normalizeNodeType(n.type) === 'addon');
    const addonById = new Map(addonNodes.map(node => [node.id, node]));

    return serviceNodes.map(service => {
      // Addons are connected by service -> addon edges (DATABASE/CACHE/QUEUE/SEARCH/ADDON/etc.).
      // We match by actual target node type instead of hardcoded prefixes/edge names.
      const ownedAddons: TopologyNode[] = [];
      for (const edge of data.edges) {
        if (edge.source !== service.id) continue;
        const addon = addonById.get(edge.target);
        if (!addon) continue;
        ownedAddons.push(addon);
      }

      return {
        service,
        addons: ownedAddons
      };
    });
  }, [data]);

  const [currentSystemIndex, setCurrentSystemIndex] = useState(0);
  const [selectedNode, setSelectedNode] = useState<TopologyNode | null>(null);

  // Initialize Three.js
  useEffect(() => {
    const containerEl = containerRef.current;
    if (!containerEl) return;

    // Scene
    const scene = new THREE.Scene();
    scene.background = new THREE.Color('#04070f');
    scene.fog = new THREE.FogExp2('#04070f', 0.002);
    sceneRef.current = scene;

    // Camera
    const camera = new THREE.PerspectiveCamera(60, containerEl.clientWidth / containerEl.clientHeight, 0.1, 1000);
    camera.position.set(0, 20, 40);
    camera.lookAt(0, 0, 0);
    cameraRef.current = camera;

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(containerEl.clientWidth, containerEl.clientHeight);
    renderer.setPixelRatio(window.devicePixelRatio);
    containerEl.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    // Controls
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.target.set(0, 0, 0);
    controls.update();
    controlsRef.current = controls;

    // Lights
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.1);
    scene.add(ambientLight);

    const pointLight = new THREE.PointLight(0xffffff, 2, 100);
    pointLight.position.set(0, 0, 0); // Sun is the light source
    scene.add(pointLight);

    const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
    directionalLight.position.set(20, 30, 15);
    scene.add(directionalLight);

    // Stars particles background
    const starsGeometry = new THREE.BufferGeometry();
    const starsCount = 2000;
    const posArray = new Float32Array(starsCount * 3);
    for(let i = 0; i < starsCount * 3; i++) {
        posArray[i] = (Math.random() - 0.5) * 400;
    }
    starsGeometry.setAttribute('position', new THREE.BufferAttribute(posArray, 3));
    const starsMaterial = new THREE.PointsMaterial({ size: 0.2, color: 0xffffff, transparent: true, opacity: 0.8 });
    const starField = new THREE.Points(starsGeometry, starsMaterial);
    scene.add(starField);

    // Cleanup
    return () => {
      cancelAnimationFrame(frameIdRef.current);
      if (rendererRef.current && containerEl.contains(rendererRef.current.domElement)) {
        containerEl.removeChild(rendererRef.current.domElement);
      }
      renderer.dispose();
    };
  }, []);

  // Update scene when current system changes
  useEffect(() => {
    if (!sceneRef.current || systems.length === 0) return;

    const scene = sceneRef.current;

    // Clear previous system meshes
    const toRemove: THREE.Object3D[] = [];
    scene.traverse((obj) => {
      if (obj.userData.isSystemObj) toRemove.push(obj);
    });
    toRemove.forEach(obj => scene.remove(obj));

    const currentSystem = systems[currentSystemIndex];
    if (!currentSystem) return;

    const { service, addons } = currentSystem;
    const systemObjects: any[] = [];

    // --- Star (Service) ---
    const serviceStatus = String(service.data.status || '').toUpperCase();
    const starColor = serviceStatus === 'ACTIVE' ? 0x10b981 :
                      serviceStatus === 'FAILED' ? 0xef4444 : 0x3b82f6;

    const starGeometry = new THREE.SphereGeometry(4, 32, 32);
    const starMaterial = new THREE.MeshPhongMaterial({
        color: starColor,
        emissive: starColor,
        emissiveIntensity: 1.6,
        shininess: 80
    });
    const starMesh = new THREE.Mesh(starGeometry, starMaterial);
    starMesh.userData = { isSystemObj: true, type: 'SERVICE', node: service };
    scene.add(starMesh);
    systemObjects.push(starMesh); // Add star to intersectable objects

    // Add Glow Sprite
    const spriteMaterial = new THREE.SpriteMaterial({
        map: null,
        color: starColor,
        transparent: true,
        blending: THREE.AdditiveBlending,
        opacity: 0.6
    });
    // Create simple radial texture
    const canvas = document.createElement('canvas');
    canvas.width = 64; canvas.height = 64;
    const ctx = canvas.getContext('2d');
    if (ctx) {
        const grad = ctx.createRadialGradient(32,32,0, 32,32,32);
        grad.addColorStop(0, 'rgba(255,255,255,1)');
        grad.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = grad;
        ctx.fillRect(0,0,64,64);
        spriteMaterial.map = new THREE.CanvasTexture(canvas);
    }
    const sprite = new THREE.Sprite(spriteMaterial);
    sprite.scale.set(12, 12, 1);
    sprite.userData = { isSystemObj: true }; // Not clickable
    starMesh.add(sprite);


    // --- Planets (Addons) ---
    addons.forEach((addon, index) => {
        const addonKind = String(addon.data.kind || '').toUpperCase();
        const planetColor =
            addonKind === 'DATABASE' ? 0xa78bfa : // Purple
            addonKind === 'CACHE' ? 0xf472b6 :    // Pink
            addonKind === 'QUEUE' ? 0xfbbf24 :    // Amber
            0x9ca3af; // Gray

        const size = Math.random() * 1 + 0.8; // Random size 0.8 - 1.8
        const geometry = new THREE.SphereGeometry(size, 16, 16);
        const material = new THREE.MeshPhongMaterial({ color: planetColor, shininess: 50 });
        const planet = new THREE.Mesh(geometry, material);

        // Orbit parameters
        const distance = 8 + (index * 3) + (Math.random() * 2);
        const speed = 0.005 + (Math.random() * 0.01);
        const angle = Math.random() * Math.PI * 2;

        planet.userData = {
            isSystemObj: true,
            type: 'ADDON',
            node: addon,
            distance,
            angle,
            speed
        };
        planet.position.x = Math.cos(angle) * distance;
        planet.position.z = Math.sin(angle) * distance;

        // Orbit path (visual ring)
        const pathGeo = new THREE.RingGeometry(distance - 0.05, distance + 0.05, 64);
        const pathMat = new THREE.MeshBasicMaterial({ color: 0xffffff, opacity: 0.1, transparent: true, side: THREE.DoubleSide });
        const pathMesh = new THREE.Mesh(pathGeo, pathMat);
        pathMesh.rotation.x = Math.PI / 2;
        pathMesh.userData = { isSystemObj: true }; // Not clickable
        scene.add(pathMesh);

        scene.add(planet);
        systemObjects.push(planet);
    });

    systemsRef.current = systemObjects;
    const renderer = rendererRef.current;
    const camera = cameraRef.current;
    if (renderer && camera) {
      renderer.render(scene, camera);
    }

  }, [systems, currentSystemIndex]);

  // Click Handler (Raycasting)
  useEffect(() => {
     const container = containerRef.current;
     if (!container) return;

     const raycaster = new THREE.Raycaster();
     const mouse = new THREE.Vector2();

     const onClick = (event: MouseEvent) => {
        if (!container || !cameraRef.current) return;

        const rect = container.getBoundingClientRect();
        mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

        raycaster.setFromCamera(mouse, cameraRef.current);

        const intersects = raycaster.intersectObjects(systemsRef.current);
        if (intersects.length > 0) {
            const hit = intersects[0].object;
            if (hit.userData.node) {
                setSelectedNode(hit.userData.node);
            }
        }
     };

     container.addEventListener('click', onClick);
     return () => container.removeEventListener('click', onClick);

  }, []);

  // Animation Loop
  useEffect(() => {
    if (!rendererRef.current || !sceneRef.current || !cameraRef.current) return;

    const animate = () => {
        frameIdRef.current = requestAnimationFrame(animate);

        if (controlsRef.current) controlsRef.current.update();

        // Animate planets
        systemsRef.current.forEach(obj => {
            if (obj.userData.type === 'ADDON') {
                obj.userData.angle += obj.userData.speed;
                obj.position.x = Math.cos(obj.userData.angle) * obj.userData.distance;
                obj.position.z = Math.sin(obj.userData.angle) * obj.userData.distance;
                obj.rotation.y += 0.02; // Self rotation
            }
             if (obj.userData.type === 'SERVICE') {
                obj.rotation.y += 0.005; // Slow star rotation
             }
        });

        rendererRef.current!.render(sceneRef.current!, cameraRef.current!);
    };
    animate();

    // Handle resize
    const handleResize = () => {
        if (!containerRef.current || !cameraRef.current || !rendererRef.current) return;
        cameraRef.current.aspect = containerRef.current.clientWidth / containerRef.current.clientHeight;
        cameraRef.current.updateProjectionMatrix();
        rendererRef.current.setSize(containerRef.current.clientWidth, containerRef.current.clientHeight);
    };
    window.addEventListener('resize', handleResize);

    return () => window.removeEventListener('resize', handleResize);
  }, []);

  const handleNext = () => {
      setCurrentSystemIndex((prev) => (prev + 1) % systems.length);
  };

  const handlePrev = () => {
      setCurrentSystemIndex((prev) => (prev - 1 + systems.length) % systems.length);
  };

  if (loading) return <div className="flex h-full items-center justify-center"><Loader2 className="w-8 h-8 animate-spin text-zinc-500" /></div>;
  if (error) return <div className="flex h-full items-center justify-center text-red-500">Error: {error.message}</div>;
  if (systems.length === 0) return <div className="flex h-full items-center justify-center text-zinc-500">No active services to visualize.</div>;

  const currentSystem = systems[currentSystemIndex];

  // Safeguard against data changes causing index out of bounds
  if (!currentSystem) return <div className="flex h-full items-center justify-center text-zinc-500">System not found.</div>;

  return (
    <div className="relative h-full w-full bg-[#04070f] overflow-hidden">
      <div ref={containerRef} className="h-full w-full cursor-move" />

      {/* HUD Overlay */}
      <div className="absolute top-0 left-0 w-full p-6 flex justify-between items-start pointer-events-none">
          <div className="space-y-1">
              <h2 className="text-2xl font-bold text-white tracking-tight font-display">{currentSystem.service.data.name}</h2>
              <div className="flex items-center gap-2">
                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${
                      currentSystem.service.data.status === 'ACTIVE' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-zinc-800 text-zinc-400'
                  }`}>
                      {currentSystem.service.data.status}
                  </span>
                  <span className="text-zinc-500 text-sm">System {currentSystemIndex + 1} of {systems.length}</span>
              </div>
          </div>
      </div>

      {/* Navigation Controls */}
      <div className="absolute bottom-8 left-1/2 -translate-x-1/2 flex items-center gap-4">
          <Button
            variant="outline"
            size="icon"
            onClick={handlePrev}
            className="rounded-full bg-black/40 border-zinc-700 hover:bg-zinc-800 backdrop-blur-md"
          >
              <ArrowLeft className="w-5 h-5" />
          </Button>
          <div className="bg-black/40 border border-zinc-700 px-4 py-2 rounded-full backdrop-blur-md text-xs text-zinc-300">
             {currentSystem.addons.length} Satellites
          </div>
          <Button
            variant="outline"
            size="icon"
            onClick={handleNext}
            className="rounded-full bg-black/40 border-zinc-700 hover:bg-zinc-800 backdrop-blur-md"
          >
              <ArrowRight className="w-5 h-5" />
          </Button>
      </div>

      {/* Legend */}
      <div className="absolute bottom-8 right-8 text-xs text-zinc-500 space-y-1 text-right pointer-events-none">
          <div>Purple = Database</div>
          <div>Pink = Cache</div>
          <div>Amber = Queue</div>
          <div>Click object for details</div>
      </div>

      {selectedNode && (
        <ServiceSidePanel node={selectedNode} onClose={() => setSelectedNode(null)} />
      )}
    </div>
  );
}

export function SolarSystemView() {
  return (
    <ErrorBoundary fallback={<div className="flex items-center justify-center h-full text-red-500">Failed to render Solar System View.</div>}>
      <SolarSystemContent />
    </ErrorBoundary>
  );
}
