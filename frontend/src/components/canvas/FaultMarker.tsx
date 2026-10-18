"use client";

import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { GLOBE_RADIUS, lonLatToVec3 } from "@/lib/geo";
import { useMeshStore } from "@/store/useMeshStore";

/** Microscopic signal-crimson crosshair at the fracture coordinate. */
export default function FaultMarker() {
  const incident = useMeshStore((s) => s.snapshot?.incident ?? null);
  const coord = incident?.fault_coordinate ?? null;
  const ring = useRef<THREE.LineLoop>(null);

  const geom = useMemo(() => {
    if (!coord) return null;
    const p = lonLatToVec3(coord.lon, coord.lat, GLOBE_RADIUS + 0.004);
    const n = p.clone().normalize();
    const east = new THREE.Vector3(0, 1, 0).cross(n).normalize();
    const north = n.clone().cross(east).normalize();
    const arm = 0.018;
    const gap = 0.005;
    const pts: THREE.Vector3[] = [];
    for (const dir of [east, east.clone().negate(), north, north.clone().negate()]) {
      pts.push(p.clone().addScaledVector(dir, gap), p.clone().addScaledVector(dir, arm));
    }
    const cross = new THREE.BufferGeometry().setFromPoints(pts);
    const circle: THREE.Vector3[] = [];
    for (let i = 0; i < 64; i++) {
      const a = (i / 64) * Math.PI * 2;
      circle.push(
        new THREE.Vector3().copy(east).multiplyScalar(Math.cos(a)).addScaledVector(north, Math.sin(a)),
      );
    }
    const ringGeom = new THREE.BufferGeometry().setFromPoints(circle);
    return { p, cross, ringGeom };
  }, [coord]);

  useFrame(() => {
    if (!ring.current || !incident) return;
    const t = (performance.now() / 1000) % 2.4;
    const s = 0.006 + (t / 2.4) * 0.03;
    ring.current.scale.setScalar(s);
    (ring.current.material as THREE.LineBasicMaterial).opacity = incident.stall_active ? 0.7 * (1 - t / 2.4) : 0;
  });

  if (!geom || !coord || !incident) return null;
  return (
    <group>
      <lineSegments geometry={geom.cross} renderOrder={5}>
        <lineBasicMaterial color="#E5534B" transparent opacity={0.95} depthWrite={false} />
      </lineSegments>
      <lineLoop ref={ring} geometry={geom.ringGeom} position={geom.p} renderOrder={5}>
        <lineBasicMaterial color="#E5534B" transparent opacity={0.6} depthWrite={false} />
      </lineLoop>
    </group>
  );
}
