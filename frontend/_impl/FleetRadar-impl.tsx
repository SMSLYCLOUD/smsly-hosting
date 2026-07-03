'use client';

/**
 * FleetRadar — Military-style radar sweep visualization of services.
 * Services arranged in concentric rings by status.
 * Radar sweep line rotates continuously with afterglow trail.
 * Unique to /services page (not shared with /topology).
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';
import * as THREE from 'three';
import { Service } from '@/lib/api';
import { useRouter } from 'next/navigation';
import { Loader2 } from 'lucide-react';

const STATUS_COLORS: Record<string, number> = {
  ACTIVE: 0x10b981,
  SUCCESS: 0x10b981,
  RUNNING: 0x10b981,
  BUILDING: 0x3b82f6,
  DEPLOYING: 0x818cf8,
  QUEUED: 0xfbbf24,
  FAILED: 0xef4444,
  CANCELLED: 0xf97316,
  STOPPED: 0x71717a,
  UNKNOWN: 0x6366f1,
};

function getStatusColor(status: string): number {
  return STATUS_COLORS[status?.toUpperCase()] || STATUS_COLORS.UNKNOWN;
}

function getStatusRing(status: string): number {
  // Active → inner, Building → middle, Failed/Unknown → outer
  switch (status?.toUpperCase()) {
    case 'ACTIVE': case 'SUCCESS': case 'RUNNING': return 0;
    case 'BUILDING': case 'DEPLOYING': case 'QUEUED': return 1;
    case 'FAILED': case 'CANCELLED': return 2;
    default: return 2;
  }
}

interface FleetRadarProps {
  services: Service[];
}

export function FleetRadar({ services }: FleetRadarProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const animationRef = useRef<number>(0);
  const sweepRef = useRef<THREE.Mesh | null>(null);
  const blipsRef = useRef<Map<string, THREE.Group>>(new Map());
  const raycasterRef = useRef(new THREE.Raycaster());
  const mouseRef = useRef(new THREE.Vector2());
  const router = useRouter();
  const [selectedService, setSelectedService] = useState<Service | null>(null);
  const [ready, setReady] = useState(false);

  const initScene = useCallback(() => {
    if (!containerRef.current) return;

    const container = containerRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight;

    // Scene
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x04070f);
    scene.fog = new THREE.Fog(0x04070f, 80, 200);
    sceneRef.current = scene;

    // Camera — top-down perspective
    const camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 500);
    camera.position.set(0, 55, 35);
    camera.lookAt(0, 0, 0);
    cameraRef.current = camera;

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    // Lights
    scene.add(new THREE.AmbientLight(0x1a1a3e, 0.6));
    const dirLight = new THREE.DirectionalLight(0x10b981, 0.3);
    dirLight.position.set(10, 30, 10);
    scene.add(dirLight);

    // Ground grid (radar circles)
    const ringRadii = [12, 24, 36];
    ringRadii.forEach((radius, i) => {
      const ringGeo = new THREE.RingGeometry(radius - 0.08, radius + 0.08, 128);
      const ringMat = new THREE.MeshBasicMaterial({
        color: 0x10b981,
        transparent: true,
        opacity: 0.15 - i * 0.03,
        side: THREE.DoubleSide,
      });
      const ring = new THREE.Mesh(ringGeo, ringMat);
      ring.rotation.x = -Math.PI / 2;
      ring.position.y = -0.1;
      scene.add(ring);
    });

    // Cross-hairs
    for (let angle = 0; angle < Math.PI; angle += Math.PI / 4) {
      const points = [
        new THREE.Vector3(-38 * Math.cos(angle), 0, -38 * Math.sin(angle)),
        new THREE.Vector3(38 * Math.cos(angle), 0, 38 * Math.sin(angle)),
      ];
      const lineGeo = new THREE.BufferGeometry().setFromPoints(points);
      const lineMat = new THREE.LineBasicMaterial({ color: 0x10b981, transparent: true, opacity: 0.08 });
      scene.add(new THREE.Line(lineGeo, lineMat));
    }

    // Radar sweep (glowing wedge)
    const sweepGeo = new THREE.CircleGeometry(38, 64, 0, Math.PI / 6);
    const sweepMat = new THREE.MeshBasicMaterial({
      color: 0x10b981,
      transparent: true,
      opacity: 0.12,
      side: THREE.DoubleSide,
    });
    const sweep = new THREE.Mesh(sweepGeo, sweepMat);
    sweep.rotation.x = -Math.PI / 2;
    sweep.position.y = 0.05;
    scene.add(sweep);
    sweepRef.current = sweep;

    // Center pulse
    const centerGeo = new THREE.SphereGeometry(1.2, 16, 16);
    const centerMat = new THREE.MeshBasicMaterial({ color: 0x10b981, transparent: true, opacity: 0.8 });
    const center = new THREE.Mesh(centerGeo, centerMat);
    center.position.y = 0.5;
    scene.add(center);

    // Outer ring glow
    const outerGlowGeo = new THREE.RingGeometry(37, 38.5, 128);
    const outerGlowMat = new THREE.MeshBasicMaterial({
      color: 0x10b981, transparent: true, opacity: 0.06, side: THREE.DoubleSide,
    });
    const outerGlow = new THREE.Mesh(outerGlowGeo, outerGlowMat);
    outerGlow.rotation.x = -Math.PI / 2;
    outerGlow.position.y = -0.15;
    scene.add(outerGlow);

    setReady(true);
  }, []);

  // Place service blips
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene || !ready) return;

    // Clear old blips
    blipsRef.current.forEach(group => scene.remove(group));
    blipsRef.current.clear();

    // Group by ring
    const rings: Service[][] = [[], [], []];
    services.forEach(svc => {
      const ring = getStatusRing(svc.latest_deployment?.status || 'UNKNOWN');
      rings[ring].push(svc);
    });

    const ringRadii = [10, 22, 33];

    rings.forEach((ringServices, ringIdx) => {
      const radius = ringRadii[ringIdx];
      ringServices.forEach((svc, svcIdx) => {
        const angle = (svcIdx / Math.max(ringServices.length, 1)) * Math.PI * 2;
        const x = radius * Math.cos(angle);
        const z = radius * Math.sin(angle);

        const group = new THREE.Group();
        group.position.set(x, 0.5, z);
        (group as any).__serviceId = svc.id;
        (group as any).__service = svc;

        const status = svc.latest_deployment?.status || 'UNKNOWN';
        const color = getStatusColor(status);

        // Blip dot
        const blipGeo = new THREE.SphereGeometry(0.8, 12, 12);
        const blipMat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.95 });
        group.add(new THREE.Mesh(blipGeo, blipMat));

        // Glow halo
        const haloGeo = new THREE.SphereGeometry(1.8, 12, 12);
        const haloMat = new THREE.MeshBasicMaterial({
          color, transparent: true, opacity: 0.15, side: THREE.BackSide,
        });
        group.add(new THREE.Mesh(haloGeo, haloMat));

        // Ring around blip
        const blipRingGeo = new THREE.RingGeometry(1.3, 1.5, 32);
        const blipRingMat = new THREE.MeshBasicMaterial({
          color, transparent: true, opacity: 0.3, side: THREE.DoubleSide,
        });
        const blipRing = new THREE.Mesh(blipRingGeo, blipRingMat);
        blipRing.rotation.x = -Math.PI / 2;
        group.add(blipRing);

        // Name label (sprite)
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d')!;
        canvas.width = 256;
        canvas.height = 64;
        ctx.clearRect(0, 0, 256, 64);
        ctx.font = 'bold 22px Inter, system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillStyle = '#e4e4e7';
        const name = svc.name.length > 16 ? svc.name.slice(0, 14) + '…' : svc.name;
        ctx.fillText(name, 128, 24);
        ctx.font = '14px Inter, system-ui, sans-serif';
        ctx.fillStyle = `#${color.toString(16).padStart(6, '0')}`;
        ctx.fillText(status, 128, 48);

        const texture = new THREE.CanvasTexture(canvas);
        const spriteMat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
        const sprite = new THREE.Sprite(spriteMat);
        sprite.scale.set(8, 2, 1);
        sprite.position.set(0, 3.5, 0);
        group.add(sprite);

        scene.add(group);
        blipsRef.current.set(svc.id, group);
      });
    });
  }, [services, ready]);

  // Animation loop
  useEffect(() => {
    if (!ready) return;

    let angle = 0;
    const animate = () => {
      animationRef.current = requestAnimationFrame(animate);

      // Rotate sweep
      angle += 0.008;
      if (sweepRef.current) {
        sweepRef.current.rotation.z = angle;
      }

      // Pulse blips when sweep passes
      blipsRef.current.forEach((group) => {
        const blipAngle = Math.atan2(group.position.z, group.position.x);
        const sweepAngle = (angle % (Math.PI * 2)) - Math.PI;
        const diff = Math.abs(blipAngle - sweepAngle);
        if (diff < 0.3 || diff > Math.PI * 2 - 0.3) {
          // Brighten when swept
          group.children.forEach(child => {
            if (child instanceof THREE.Mesh && child.material instanceof THREE.MeshBasicMaterial) {
              child.material.opacity = Math.min(1, child.material.opacity + 0.05);
            }
          });
        } else {
          // Fade back
          group.children.forEach((child, i) => {
            if (child instanceof THREE.Mesh && child.material instanceof THREE.MeshBasicMaterial) {
              const target = i === 0 ? 0.95 : i === 1 ? 0.15 : 0.3;
              child.material.opacity += (target - child.material.opacity) * 0.02;
            }
          });
        }
      });

      rendererRef.current?.render(sceneRef.current!, cameraRef.current!);
    };

    animate();
    return () => cancelAnimationFrame(animationRef.current);
  }, [ready]);

  // Resize
  useEffect(() => {
    const handleResize = () => {
      if (!containerRef.current || !rendererRef.current || !cameraRef.current) return;
      const w = containerRef.current.clientWidth;
      const h = containerRef.current.clientHeight;
      rendererRef.current.setSize(w, h);
      cameraRef.current.aspect = w / h;
      cameraRef.current.updateProjectionMatrix();
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // Click detection
  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      if (!containerRef.current || !cameraRef.current || !sceneRef.current) return;

      const rect = containerRef.current.getBoundingClientRect();
      mouseRef.current.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      mouseRef.current.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

      raycasterRef.current.setFromCamera(mouseRef.current, cameraRef.current);

      const blipGroups = Array.from(blipsRef.current.values());
      const allMeshes = blipGroups.flatMap(g => g.children.filter(c => c instanceof THREE.Mesh));
      const intersects = raycasterRef.current.intersectObjects(allMeshes, false);

      if (intersects.length > 0) {
        const hit = intersects[0].object;
        const parentGroup = hit.parent;
        if (parentGroup && (parentGroup as any).__serviceId) {
          const svc = (parentGroup as any).__service as Service;
          setSelectedService(svc);
        }
      } else {
        setSelectedService(null);
      }
    };

    const el = containerRef.current;
    el?.addEventListener('click', handleClick);
    return () => el?.removeEventListener('click', handleClick);
  }, []);

  // Init
  useEffect(() => {
    initScene();
    return () => {
      cancelAnimationFrame(animationRef.current);
      if (rendererRef.current && containerRef.current) {
        // Dispose all WebGL resources to prevent GPU memory leaks
        if (sceneRef.current) {
          sceneRef.current.traverse((obj) => {
            if (obj instanceof THREE.Mesh || obj instanceof THREE.Points || obj instanceof THREE.Line) {
              obj.geometry?.dispose();
              if (Array.isArray(obj.material)) {
                obj.material.forEach((m) => { (m as any).map?.dispose(); m.dispose(); });
              } else if (obj.material) {
                (obj.material as any).map?.dispose();
                (obj.material as THREE.Material).dispose();
              }
            }
            if (obj instanceof THREE.Sprite) {
              (obj.material as THREE.SpriteMaterial).map?.dispose();
              obj.material.dispose();
            }
          });
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
        containerRef.current.removeChild(rendererRef.current.domElement);
        rendererRef.current.dispose();
      }
    };
  }, [initScene]);

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#04070f]">
      <div ref={containerRef} className="absolute inset-0" />

      {/* Legend */}
      <div className="absolute top-4 left-4 z-10 rounded-xl border border-zinc-800/80 bg-black/60 p-3 backdrop-blur-lg">
        <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-emerald-500 mb-2">
          Fleet Radar
        </div>
        <div className="text-[11px] text-zinc-500 mb-2">
          {services.length} service{services.length !== 1 ? 's' : ''} • Live sweep
        </div>
        <div className="flex flex-col gap-1.5">
          {[
            { color: '#10b981', label: 'Active', ring: 'Inner' },
            { color: '#3b82f6', label: 'Building', ring: 'Middle' },
            { color: '#ef4444', label: 'Failed', ring: 'Outer' },
          ].map(({ color, label, ring }) => (
            <div key={label} className="flex items-center gap-2">
              <div className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
              <span className="text-[11px] text-zinc-300">{label}</span>
              <span className="text-[9px] text-zinc-600">({ring})</span>
            </div>
          ))}
        </div>
      </div>

      {/* Controls hint */}
      <div className="absolute bottom-4 right-4 z-10 rounded-xl border border-zinc-800/80 bg-black/60 px-3 py-2 backdrop-blur-lg">
        <div className="text-[10px] text-zinc-400">Click blip to inspect service</div>
      </div>

      {/* Selected service panel */}
      {selectedService && (
        <div className="absolute top-4 right-4 z-20 w-64 rounded-xl border border-zinc-800 bg-black/80 backdrop-blur-xl p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-white truncate">{selectedService.name}</h3>
            <button
              onClick={() => setSelectedService(null)}
              className="text-zinc-500 hover:text-white text-xs"
            >✕</button>
          </div>
          <div className="text-[11px] text-zinc-400 space-y-1">
            <div>Status: <span className="text-emerald-400">{selectedService.latest_deployment?.status || 'UNKNOWN'}</span></div>
            {selectedService.public_domain && (
              <div className="truncate">Domain: <span className="text-blue-400">{selectedService.public_domain}</span></div>
            )}
            {selectedService.branch && (
              <div>Branch: <span className="text-violet-400">{selectedService.branch}</span></div>
            )}
          </div>
          <button
            onClick={() => router.push(`/services/${selectedService.id}`)}
            className="w-full px-3 py-1.5 bg-emerald-500/20 text-emerald-400 rounded-lg text-xs font-medium hover:bg-emerald-500/30 transition-colors"
          >
            Open Service →
          </button>
        </div>
      )}

      {!ready && (
        <div className="absolute inset-0 flex items-center justify-center">
          <Loader2 className="w-8 h-8 animate-spin text-emerald-500/50" />
        </div>
      )}
    </div>
  );
}
