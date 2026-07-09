'use client';

import { Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Cloud, Clouds, Sky, Stars } from '@react-three/drei';
import { useTheme } from 'next-themes';
import * as THREE from 'three';

/**
 * Hero atmospheric background: layered drifting clouds with theme-aware sky.
 *
 * Fixes applied vs previous implementation:
 * - Batch all clouds under a single <Clouds> (one instanced draw call)
 * - Stable seeds so remounts don't reshuffle the sky
 * - Proper lighting in both themes (MeshLambertMaterial needs lights)
 * - Rain uses a position buffer (instanceMatrix has no getX/getY)
 * - Soft density so copy remains readable
 */

type RainState = {
  positions: Float32Array;
  velocities: Float32Array;
};

function RainParticles({ count = 400 }: { count?: number }) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const dummy = useMemo(() => new THREE.Object3D(), []);

  const state = useMemo<RainState>(() => {
    const positions = new Float32Array(count * 3);
    const velocities = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const i3 = i * 3;
      positions[i3] = (Math.random() - 0.5) * 40;
      positions[i3 + 1] = Math.random() * 20 - 5;
      positions[i3 + 2] = (Math.random() - 0.5) * 12;
      velocities[i3] = (Math.random() - 0.35) * 0.03;
      velocities[i3 + 1] = -(0.18 + Math.random() * 0.28);
      velocities[i3 + 2] = 0;
    }
    return { positions, velocities };
  }, [count]);

  useFrame(() => {
    const mesh = meshRef.current;
    if (!mesh) return;

    const { positions, velocities } = state;
    for (let i = 0; i < count; i++) {
      const i3 = i * 3;
      positions[i3] += velocities[i3];
      positions[i3 + 1] += velocities[i3 + 1];

      if (positions[i3 + 1] < -10) {
        positions[i3 + 1] = 10 + Math.random() * 4;
        positions[i3] = (Math.random() - 0.5) * 40;
        positions[i3 + 2] = (Math.random() - 0.5) * 12;
      }

      dummy.position.set(positions[i3], positions[i3 + 1], positions[i3 + 2]);
      dummy.scale.set(0.012, 0.28 + (i % 5) * 0.04, 0.012);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    }
    mesh.instanceMatrix.needsUpdate = true;
  });

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, count]} frustumCulled={false}>
      <cylinderGeometry args={[0.5, 0.5, 1, 4]} />
      <meshBasicMaterial color="#94a3b8" transparent opacity={0.28} depthWrite={false} />
    </instancedMesh>
  );
}

function LightningFlash() {
  const lightRef = useRef<THREE.PointLight>(null);
  const timer = useRef(4 + Math.random() * 6);
  const flashIntensity = useRef(0);
  const flashPos = useRef(new THREE.Vector3(0, 8, -4));

  useFrame((_, delta) => {
    timer.current -= delta;
    if (timer.current <= 0) {
      flashIntensity.current = 2.2 + Math.random() * 2.5;
      flashPos.current.set((Math.random() - 0.5) * 16, 6 + Math.random() * 4, -2 - Math.random() * 6);
      // Occasional double-strike
      timer.current = Math.random() > 0.7 ? 0.12 + Math.random() * 0.2 : 7 + Math.random() * 11;
    }

    flashIntensity.current *= 0.88;
    if (lightRef.current) {
      lightRef.current.intensity = flashIntensity.current;
      lightRef.current.position.copy(flashPos.current);
    }
  });

  return (
    <pointLight
      ref={lightRef}
      color="#c7d2fe"
      intensity={0}
      distance={55}
      decay={2}
    />
  );
}

function DriftGroup({
  children,
  speed = 0.1,
  wrap = 20,
}: {
  children: ReactNode;
  speed?: number;
  wrap?: number;
}) {
  const groupRef = useRef<THREE.Group>(null);

  useFrame((_, delta) => {
    const group = groupRef.current;
    if (!group) return;
    group.children.forEach((child, i) => {
      child.position.x -= delta * (speed + i * 0.012);
      if (child.position.x < -wrap) child.position.x = wrap;
    });
  });

  return <group ref={groupRef}>{children}</group>;
}

function DayClouds() {
  return (
    <Clouds material={THREE.MeshLambertMaterial} limit={180} frustumCulled={false}>
      <DriftGroup speed={0.07} wrap={22}>
        {/* Far layer — soft, large, slow */}
        <Cloud
          seed={11}
          segments={28}
          bounds={[8, 1.6, 2]}
          volume={7}
          color="#f8fafc"
          opacity={0.55}
          fade={55}
          speed={0.08}
          growth={3}
          position={[-6, 5.2, -14]}
        />
        <Cloud
          seed={22}
          segments={24}
          bounds={[7, 1.4, 1.8]}
          volume={6}
          color="#f1f5f9"
          opacity={0.5}
          fade={50}
          speed={0.06}
          growth={2.5}
          position={[5, 5.8, -16]}
        />
        <Cloud
          seed={33}
          segments={20}
          bounds={[6, 1.2, 1.5]}
          volume={5}
          color="#e2e8f0"
          opacity={0.45}
          fade={48}
          speed={0.07}
          growth={2}
          position={[12, 4.8, -13]}
        />

        {/* Mid layer */}
        <Cloud
          seed={44}
          segments={32}
          bounds={[6, 1.8, 2]}
          volume={8}
          color="#ffffff"
          opacity={0.7}
          fade={40}
          speed={0.12}
          growth={3.5}
          position={[-2, 3.8, -8]}
        />
        <Cloud
          seed={55}
          segments={28}
          bounds={[5.5, 1.6, 1.8]}
          volume={7}
          color="#f8fafc"
          opacity={0.65}
          fade={38}
          speed={0.1}
          growth={3}
          position={[7, 4.2, -9]}
        />
        <Cloud
          seed={66}
          segments={22}
          bounds={[4.5, 1.4, 1.5]}
          volume={5.5}
          color="#e0f2fe"
          opacity={0.55}
          fade={42}
          speed={0.09}
          growth={2.5}
          position={[-10, 4.5, -10]}
        />

        {/* Near accent puffs — slightly cooler tint for depth */}
        <Cloud
          seed={77}
          segments={18}
          bounds={[3.5, 1.1, 1.2]}
          volume={4}
          color="#f0f9ff"
          opacity={0.6}
          fade={32}
          speed={0.14}
          growth={2}
          position={[1.5, 3.2, -5]}
        />
        <Cloud
          seed={88}
          segments={16}
          bounds={[3, 1, 1]}
          volume={3.5}
          color="#ffffff"
          opacity={0.5}
          fade={30}
          speed={0.11}
          growth={2}
          position={[-8, 3.5, -6]}
        />
      </DriftGroup>
    </Clouds>
  );
}

function NightClouds() {
  return (
    <>
      <Clouds material={THREE.MeshLambertMaterial} limit={220} frustumCulled={false}>
        <DriftGroup speed={0.05} wrap={22}>
          {/* Deep storm bank */}
          <Cloud
            seed={101}
            segments={40}
            bounds={[9, 2.4, 2.5]}
            volume={11}
            color="#1e293b"
            opacity={0.85}
            fade={32}
            speed={0.06}
            growth={2}
            position={[-3, 4.2, -6]}
          />
          <Cloud
            seed={112}
            segments={36}
            bounds={[7.5, 2.2, 2.2]}
            volume={9}
            color="#0f172a"
            opacity={0.8}
            fade={35}
            speed={0.05}
            growth={1.8}
            position={[6, 3.8, -8]}
          />
          <Cloud
            seed={123}
            segments={30}
            bounds={[6.5, 2, 2]}
            volume={8}
            color="#1e293b"
            opacity={0.75}
            fade={38}
            speed={0.07}
            growth={2}
            position={[-9, 5, -10]}
          />
          {/* Higher veil */}
          <Cloud
            seed={134}
            segments={24}
            bounds={[8, 1.5, 1.8]}
            volume={6}
            color="#334155"
            opacity={0.55}
            fade={45}
            speed={0.04}
            growth={1.5}
            position={[2, 5.5, -12]}
          />
          <Cloud
            seed={145}
            segments={22}
            bounds={[5, 1.4, 1.5]}
            volume={5}
            color="#475569"
            opacity={0.45}
            fade={42}
            speed={0.08}
            growth={1.5}
            position={[11, 4.6, -9]}
          />
          <Cloud
            seed={156}
            segments={20}
            bounds={[4.5, 1.3, 1.4]}
            volume={4.5}
            color="#334155"
            opacity={0.5}
            fade={40}
            speed={0.06}
            growth={1.6}
            position={[-12, 4, -7]}
          />
        </DriftGroup>
      </Clouds>
      <LightningFlash />
      <RainParticles count={420} />
    </>
  );
}

function Scene({ dark }: { dark: boolean }) {
  return (
    <>
      {dark ? (
        <>
          <color attach="background" args={['#020617']} />
          <Stars radius={120} depth={60} count={2500} factor={2.8} saturation={0} fade speed={0.4} />
          <ambientLight intensity={0.35} color="#64748b" />
          <directionalLight position={[-6, 10, 4]} intensity={0.25} color="#94a3b8" />
          <hemisphereLight args={['#1e293b', '#020617', 0.4]} />
          <NightClouds />
          <fog attach="fog" args={['#020617', 10, 38]} />
        </>
      ) : (
        <>
          <Sky
            sunPosition={[80, 28, 60]}
            turbidity={1.6}
            rayleigh={0.55}
            mieCoefficient={0.004}
            mieDirectionalG={0.85}
          />
          <ambientLight intensity={0.55} color="#e0f2fe" />
          <directionalLight position={[12, 10, 6]} intensity={0.85} color="#fef9c3" />
          <hemisphereLight args={['#bae6fd', '#f8fafc', 0.45]} />
          <DayClouds />
          <fog attach="fog" args={['#bfdbfe', 12, 42]} />
        </>
      )}
    </>
  );
}

export function NatureBackground() {
  const { resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Avoid SSR/hydration flash and empty Canvas before theme is known
  if (!mounted) {
    return (
      <div
        className="absolute inset-0 z-0 bg-gradient-to-b from-sky-100/80 via-slate-50 to-white dark:from-slate-950 dark:via-slate-950 dark:to-slate-950"
        aria-hidden
      />
    );
  }

  const dark = resolvedTheme === 'dark';

  return (
    <div className="absolute inset-0 z-0 overflow-hidden" aria-hidden>
      {/* Soft base so canvas alpha never shows body starfield through */}
      <div
        className={`absolute inset-0 ${
          dark
            ? 'bg-gradient-to-b from-slate-950 via-slate-950 to-slate-950'
            : 'bg-gradient-to-b from-sky-100 via-sky-50 to-white'
        }`}
      />

      <Canvas
        camera={{ position: [0, 2.2, 14], fov: 50, near: 0.1, far: 80 }}
        dpr={[1, 1.5]}
        gl={{
          antialias: true,
          alpha: true,
          powerPreference: 'high-performance',
        }}
        style={{ background: 'transparent' }}
        className="!absolute inset-0"
      >
        <Suspense fallback={null}>
          <Scene dark={dark} />
        </Suspense>
      </Canvas>

      {/* Readability scrim — keeps hero copy crisp over active sky */}
      <div
        className={`pointer-events-none absolute inset-0 ${
          dark
            ? 'bg-gradient-to-b from-slate-950/50 via-slate-950/25 to-slate-950/70'
            : 'bg-gradient-to-b from-white/35 via-white/15 to-white/55'
        }`}
      />

      {/* Bottom blend into the next section */}
      <div
        className={`pointer-events-none absolute inset-x-0 bottom-0 h-28 ${
          dark
            ? 'bg-gradient-to-t from-slate-950 to-transparent'
            : 'bg-gradient-to-t from-white to-transparent'
        }`}
      />
    </div>
  );
}
