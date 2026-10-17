"use client";

import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { polylineToSpline } from "@/lib/geo";
import { useMeshStore } from "@/store/useMeshStore";

/** Group velocity in silica: c / n = 299,792.458 / 1.4682 km/s. */
export const FIBRE_KM_S = 299_792.458 / 1.4682;
/** Display time scale: pulses travel at 1/100 of real speed so they are visible. */
export const TIME_SCALE = 100;

const CABLE_ALTITUDE = 0.002;

const vertexShader = /* glsl */ `
  attribute float aCable;
  attribute float aDist;
  uniform sampler2D uState;
  uniform float uCount;
  varying float vDist;
  varying vec4 vState;
  varying float vFacing;
  void main() {
    vDist = aDist;
    vState = texture2D(uState, vec2((aCable + 0.5) / uCount, 0.5));
    vec4 world = modelMatrix * vec4(position, 1.0);
    vFacing = dot(normalize(world.xyz), normalize(cameraPosition - world.xyz));
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const fragmentShader = /* glsl */ `
  uniform float uTime;
  uniform float uSpeed;      // km per second of display time
  uniform float uWavelength; // km between photon envelopes
  uniform float uSeverAge;   // seconds since the active fault, < 0 when none
  uniform vec3 uCyan;
  uniform vec3 uCore;
  uniform vec3 uCharcoal;
  uniform vec3 uAmber;
  uniform vec3 uCrimson;
  uniform float uHeat;   // 0..1 blend into the planet-scan risk view
  uniform float uFocus;  // 0..1 dim everything not highlighted by an Ask-the-graph answer
  varying float vDist;
  varying vec4 vState;
  varying float vFacing;
  void main() {
    float severed = vState.r;
    float carrier = vState.g;
    float phase = fract((vDist - uTime * uSpeed) / uWavelength);
    float envelope = pow(1.0 - phase, 10.0) + 0.35 * pow(1.0 - phase, 3.0);
    float base = mix(0.16, 0.42, carrier);
    vec3 live = uCyan * (base + envelope * mix(0.55, 1.1, carrier)) + uCore * envelope * envelope * 0.6 * carrier;
    float alive = 1.0;
    if (severed > 0.5) {
      // The glass cools: the pulse dies and the run settles to cold charcoal.
      alive = 1.0 - smoothstep(0.0, 1.6, max(uSeverAge, 0.0));
    }
    vec3 col = mix(uCharcoal * 1.6, live, alive);
    float limb = smoothstep(-0.05, 0.25, vFacing);
    float alpha = mix(0.85, 0.55 + 0.45 * envelope, alive) * limb;

    // Planet scan: colour every cable by the risk FalkorDB measured when it was cut.
    float heat = vState.b;
    vec3 heatCol = heat < 0.02 ? uCyan * 0.18
      : mix(mix(uCyan * 0.55, uAmber, smoothstep(0.0, 0.45, heat)), uCrimson, smoothstep(0.45, 1.0, heat));
    float heatAlpha = (heat < 0.02 ? 0.22 : 0.4 + 0.6 * heat) * limb;
    col = mix(col, heatCol * (0.85 + 0.7 * envelope * heat), uHeat);
    alpha = mix(alpha, heatAlpha, uHeat);

    // Ask the graph: light the cables in the answer, dim the rest.
    float lit = vState.a;
    col = mix(col, mix(col * 0.22, uAmber * (0.85 + 0.9 * envelope), lit), uFocus);
    alpha = mix(alpha, mix(alpha * 0.3, 0.95 * limb, lit), uFocus);

    gl_FragColor = vec4(col, alpha);
  }
`;

export default function CableSplines() {
  const cables = useMeshStore((s) => s.cables);
  const snapshot = useMeshStore((s) => s.snapshot);
  const tunnels = useMeshStore((s) => s.tunnels);
  const clockOffset = useMeshStore((s) => s.clockOffset);
  const mode = useMeshStore((s) => s.mode);
  const heatMap = useMeshStore((s) => s.scan.heat);
  const askHighlight = useMeshStore((s) => s.askState.result?.highlight?.cables);

  const index = useMemo(() => new Map(cables.map((c, i) => [c.id, i])), [cables]);

  const geometry = useMemo(() => {
    const pos: number[] = [];
    const cab: number[] = [];
    const dist: number[] = [];
    cables.forEach((c, ci) => {
      for (const line of c.lines) {
        if (line.length < 2) continue;
        const { points, distKm } = polylineToSpline(line, CABLE_ALTITUDE);
        for (let i = 1; i < points.length; i++) {
          const a = points[i - 1];
          const b = points[i];
          pos.push(a.x, a.y, a.z, b.x, b.y, b.z);
          cab.push(ci, ci);
          dist.push(distKm[i - 1], distKm[i]);
        }
      }
    });
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("aCable", new THREE.Float32BufferAttribute(cab, 1));
    g.setAttribute("aDist", new THREE.Float32BufferAttribute(dist, 1));
    return g;
  }, [cables]);

  const stateTex = useMemo(() => {
    const n = Math.max(1, cables.length);
    const tex = new THREE.DataTexture(new Uint8Array(n * 4), n, 1, THREE.RGBAFormat);
    tex.magFilter = THREE.NearestFilter;
    tex.minFilter = THREE.NearestFilter;
    tex.needsUpdate = true;
    return tex;
  }, [cables.length]);

  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader,
        fragmentShader,
        transparent: true,
        depthWrite: false,
        uniforms: {
          uState: { value: stateTex },
          uCount: { value: Math.max(1, cables.length) },
          uTime: { value: 0 },
          uSpeed: { value: FIBRE_KM_S / TIME_SCALE },
          uWavelength: { value: 900 },
          uSeverAge: { value: -1 },
          uCyan: { value: new THREE.Color("#38BDF8") },
          uCore: { value: new THREE.Color("#E0F2FE") },
          uCharcoal: { value: new THREE.Color("#1E293B") },
          uAmber: { value: new THREE.Color("#FDE68A") },
          uCrimson: { value: new THREE.Color("#E11D48") },
          uHeat: { value: 0 },
          uFocus: { value: 0 },
        },
      }),
    [stateTex, cables.length],
  );

  // Per-cable state: R = severed, G = carries a logical tunnel, B = scan risk, A = in an Ask answer.
  useEffect(() => {
    const data = stateTex.image.data as Uint8Array;
    data.fill(0);
    for (const [cid, h] of Object.entries(heatMap)) {
      const i = index.get(cid);
      if (i !== undefined) data[i * 4 + 2] = Math.round(Math.max(0, Math.min(1, h)) * 255);
    }
    for (const cid of askHighlight ?? []) {
      const i = index.get(cid);
      if (i !== undefined) data[i * 4 + 3] = 255;
    }
    for (const t of tunnels) {
      for (const cid of t.carried_by) {
        const i = index.get(cid);
        if (i !== undefined) data[i * 4 + 1] = 255;
      }
    }
    const cut = snapshot?.incident?.cable_id;
    const ci = cut ? index.get(cut) : undefined;
    if (ci !== undefined) {
      data[ci * 4] = 255;
      data[ci * 4 + 1] = 255;
    }
    stateTex.needsUpdate = true;
  }, [snapshot?.incident?.cable_id, tunnels, index, stateTex, heatMap, askHighlight]);

  const startedAt = snapshot?.incident?.started_at ?? null;
  const t0 = useRef(performance.now());
  useFrame((_, dt) => {
    const u = material.uniforms;
    const heatGoal = mode === "scan" ? 1 : 0;
    const focusGoal = mode === "ask" && (askHighlight?.length ?? 0) > 0 ? 1 : 0;
    u.uHeat.value = THREE.MathUtils.damp(u.uHeat.value, heatGoal, 3, dt);
    u.uFocus.value = THREE.MathUtils.damp(u.uFocus.value, focusGoal, 3, dt);
    u.uTime.value = (performance.now() - t0.current) / 1000;
    u.uSeverAge.value = startedAt ? Date.now() / 1000 + clockOffset - startedAt : -1;
  });

  useEffect(() => () => {
    geometry.dispose();
    material.dispose();
    stateTex.dispose();
  }, [geometry, material, stateTex]);

  if (!cables.length) return null;
  return <lineSegments geometry={geometry} material={material} renderOrder={2} />;
}
