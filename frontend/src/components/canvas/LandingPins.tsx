"use client";

import { useMemo, useRef } from "react";
import { Html } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { GLOBE_RADIUS, haversineKm, lonLatToVec3 } from "@/lib/geo";
import type { Station } from "@/lib/types";
import { useMeshStore } from "@/store/useMeshStore";

const PIN_ALTITUDE = 0.003;

const vertexShader = /* glsl */ `
  attribute float aKind;
  varying float vKind;
  varying float vFacing;
  void main() {
    vKind = aKind;
    vec4 world = modelMatrix * vec4(position, 1.0);
    vFacing = dot(normalize(world.xyz), normalize(cameraPosition - world.xyz));
    vec4 mv = viewMatrix * world;
    gl_PointSize = (aKind > 0.5 ? 9.0 : 3.2) * (2.2 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`;

const fragmentShader = /* glsl */ `
  varying float vKind;
  varying float vFacing;
  void main() {
    vec2 c = gl_PointCoord - 0.5;
    float r = length(c);
    if (r > 0.5) discard;
    float alpha;
    vec3 col;
    if (vKind > 0.5) {
      // Data-centre gateway: thin silica ring with a warm core.
      float ring = smoothstep(0.5, 0.42, r) * smoothstep(0.30, 0.38, r);
      float core = smoothstep(0.16, 0.0, r);
      col = mix(vec3(0.973, 0.980, 0.988), vec3(0.992, 0.902, 0.541), core);
      alpha = max(ring, core);
    } else {
      col = vec3(0.886, 0.949, 0.996);
      alpha = smoothstep(0.5, 0.1, r) * 0.55;
    }
    alpha *= smoothstep(0.0, 0.2, vFacing);
    gl_FragColor = vec4(col, alpha);
  }
`;

type Tone = "silica" | "amber" | "crimson";

/** Leader-line placements tried in order when labels would collide. */
const PLACEMENTS = [
  { up: true, right: true, len: 28 },
  { up: false, right: true, len: 26 },
  { up: true, right: false, len: 54 },
] as const;
const COLLISION_KM = 650;

function Label({ station, tone, placement }: { station: Station; tone: Tone; placement: number }) {
  const anchor = useMemo(() => lonLatToVec3(station.lon, station.lat, GLOBE_RADIUS + PIN_ALTITUDE), [station]);
  const ref = useRef<HTMLDivElement>(null);
  const { camera } = useThree();
  const tmp = useMemo(() => new THREE.Vector3(), []);
  useFrame(() => {
    if (!ref.current) return;
    const facing = tmp.copy(camera.position).sub(anchor).normalize().dot(anchor.clone().normalize());
    ref.current.style.opacity = String(THREE.MathUtils.smoothstep(facing, 0.05, 0.3));
  });
  const color = tone === "amber" ? "text-amber-laser" : tone === "crimson" ? "text-crimson" : "text-ink";
  const rule = tone === "amber" ? "bg-amber-laser/60" : tone === "crimson" ? "bg-crimson/70" : "bg-silica/40";
  const p = PLACEMENTS[placement];
  const elbowY = p.up ? -p.len : p.len;
  return (
    <Html position={anchor} zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
      <div ref={ref} className="relative h-0 w-0 select-none transition-opacity duration-300">
        <div className={`absolute w-px ${rule}`} style={{ left: 0, top: p.up ? -p.len : 0, height: p.len }} />
        <div className={`absolute h-px w-3 ${rule}`} style={{ top: elbowY, left: p.right ? 0 : -12 }} />
        <div
          className={`absolute whitespace-nowrap font-mono text-hud ${color}`}
          style={{
            top: elbowY - 7,
            left: p.right ? 16 : -16,
            transform: p.right ? undefined : "translateX(-100%)",
          }}
        >
          <span className="tracking-[0.14em]">{station.code}</span>
        </div>
      </div>
    </Html>
  );
}

export default function LandingPins() {
  const stations = useMeshStore((s) => s.stations);
  const detour = useMeshStore((s) => s.detour);
  const incident = useMeshStore((s) => s.snapshot?.incident ?? null);
  const mode = useMeshStore((s) => s.mode);
  const askStations = useMeshStore((s) => s.askState.result?.highlight?.stations);

  const geometry = useMemo(() => {
    const pos: number[] = [];
    const kind: number[] = [];
    const v = new THREE.Vector3();
    for (const st of stations) {
      lonLatToVec3(st.lon, st.lat, GLOBE_RADIUS + PIN_ALTITUDE, v);
      pos.push(v.x, v.y, v.z);
      kind.push(st.kind === "DC_GATEWAY" ? 1 : 0);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("aKind", new THREE.Float32BufferAttribute(kind, 1));
    return g;
  }, [stations]);

  const material = useMemo(
    () => new THREE.ShaderMaterial({ vertexShader, fragmentShader, transparent: true, depthWrite: false }),
    [],
  );

  const byCode = useMemo(() => new Map(stations.map((s) => [s.code, s])), [stations]);

  // Diegetic labels in priority order: gateways, then detour landfalls. Nothing else is labelled.
  // Nearby labels take alternate leader placements; a label with no free placement is dropped.
  const labels = useMemo(() => {
    const wanted = new Map<string, Tone>();
    if (mode === "ask") for (const code of (askStations ?? []).slice(0, 10)) wanted.set(code, "amber");
    if (mode === "scan") return [];
    for (const st of stations) if (st.kind === "DC_GATEWAY" && !wanted.has(st.code)) wanted.set(st.code, "silica");
    for (const hop of mode === "incident" ? (detour?.hops ?? []) : []) {
      if (hop.medium !== "SUBSEA") continue;
      if (!wanted.has(hop.from)) wanted.set(hop.from, "amber");
      if (!wanted.has(hop.to)) wanted.set(hop.to, "amber");
    }
    const placed: { station: Station; tone: Tone; placement: number }[] = [];
    for (const [code, tone] of wanted) {
      const station = byCode.get(code);
      if (!station) continue;
      const taken = new Set(
        placed
          .filter((p) => haversineKm([p.station.lon, p.station.lat], [station.lon, station.lat]) < COLLISION_KM)
          .map((p) => p.placement),
      );
      const placement = PLACEMENTS.findIndex((_, i) => !taken.has(i));
      if (placement >= 0) placed.push({ station, tone, placement });
    }
    return placed;
  }, [stations, incident, detour, byCode, mode, askStations]);

  if (!stations.length) return null;
  return (
    <group>
      <points geometry={geometry} material={material} renderOrder={4} />
      {labels.map((l) => (
        <Label key={l.station.code} station={l.station} tone={l.tone} placement={l.placement} />
      ))}
    </group>
  );
}
