"use client";

import { useEffect, useMemo } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { polylineToSpline } from "@/lib/geo";
import type { Hop, SpanGeometry } from "@/lib/types";
import { useMeshStore } from "@/store/useMeshStore";
import { FIBRE_KM_S, TIME_SCALE } from "./CableSplines";

const DETOUR_ALTITUDE = 0.0045;
const IGNITION_MS = 2600;

/** Geometry is stored once per undirected span from its original 'a' end. */
export function hopCoords(hop: Hop, spanGeo: SpanGeometry): [number, number][] | null {
  const fwd = spanGeo[`${hop.cable}:${hop.from}-${hop.to}`];
  if (fwd) return fwd;
  const rev = spanGeo[`${hop.cable}:${hop.to}-${hop.from}`];
  return rev ? [...rev].reverse() : null;
}

const vertexShader = /* glsl */ `
  attribute float aDist;
  varying float vDist;
  varying float vFacing;
  void main() {
    vDist = aDist;
    vec4 world = modelMatrix * vec4(position, 1.0);
    vFacing = dot(normalize(world.xyz), normalize(cameraPosition - world.xyz));
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const fragmentShader = /* glsl */ `
  uniform float uTime;
  uniform float uSpeed;
  uniform float uReveal;  // 0..1 share of the corridor ignited
  uniform float uTotal;   // corridor length, km
  uniform vec3 uPearl;
  uniform vec3 uAmber;
  varying float vDist;
  varying float vFacing;
  void main() {
    float front = uReveal * uTotal;
    if (vDist > front) discard;
    float phase = fract((vDist - uTime * uSpeed) / 700.0);
    float envelope = pow(1.0 - phase, 8.0);
    float head = exp(-abs(front - vDist) / 180.0) * (1.0 - step(0.999, uReveal));
    vec3 col = mix(uAmber, uPearl, 0.35 + 0.65 * envelope) * (0.75 + 0.9 * envelope) + uPearl * head * 1.6;
    float limb = smoothstep(-0.05, 0.25, vFacing);
    gl_FragColor = vec4(col, (0.8 + 0.2 * envelope) * limb);
  }
`;

export default function DetourSpline() {
  const detour = useMeshStore((s) => s.detour);
  const spanGeo = useMeshStore((s) => s.spanGeo);
  const ignitedAt = useMeshStore((s) => s.detourIgnitedAt);

  const built = useMemo(() => {
    if (!detour) return null;
    const pos: number[] = [];
    const dist: number[] = [];
    let offset = 0;
    for (const hop of detour.hops) {
      const coords = hopCoords(hop, spanGeo);
      if (!coords || coords.length < 2) continue;
      const { points, distKm } = polylineToSpline(coords, DETOUR_ALTITUDE, 24);
      for (let i = 1; i < points.length; i++) {
        const a = points[i - 1];
        const b = points[i];
        pos.push(a.x, a.y, a.z, b.x, b.y, b.z);
        dist.push(offset + distKm[i - 1], offset + distKm[i]);
      }
      offset += distKm[distKm.length - 1] ?? 0;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("aDist", new THREE.Float32BufferAttribute(dist, 1));
    return { geometry: g, total: offset };
  }, [detour, spanGeo]);

  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader,
        fragmentShader,
        transparent: true,
        depthWrite: false,
        uniforms: {
          uTime: { value: 0 },
          uSpeed: { value: FIBRE_KM_S / TIME_SCALE },
          uReveal: { value: 0 },
          uTotal: { value: 1 },
          uPearl: { value: new THREE.Color("#E0B156") },
          uAmber: { value: new THREE.Color("#F59E0B") },
        },
      }),
    [],
  );

  useFrame(() => {
    const u = material.uniforms;
    u.uTime.value = performance.now() / 1000;
    u.uTotal.value = built?.total ?? 1;
    const t = ignitedAt ? (performance.now() - ignitedAt) / IGNITION_MS : 0;
    u.uReveal.value = Math.min(1, Math.max(0, 1 - Math.pow(1 - Math.min(t, 1), 3)));
  });

  useEffect(() => () => built?.geometry.dispose(), [built]);
  useEffect(() => () => material.dispose(), [material]);

  if (!built) return null;
  return <lineSegments geometry={built.geometry} material={material} renderOrder={3} />;
}
