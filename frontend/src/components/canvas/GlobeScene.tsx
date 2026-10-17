"use client";

import { useEffect, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { lonLatToVec3 } from "@/lib/geo";
import { useMeshStore } from "@/store/useMeshStore";
import BathymetryMesh from "./BathymetryMesh";
import CableSplines from "./CableSplines";
import DetourSpline from "./DetourSpline";
import FaultMarker from "./FaultMarker";
import LandingPins from "./LandingPins";

const HOME = { lon: -38, lat: 42, dist: 2.95 };

/** Glides the camera to the fault, then frames the detour; yields to the operator once settled. */
function CameraDirector({ controls }: { controls: React.RefObject<OrbitControlsImpl> }) {
  const { camera } = useThree();
  const incident = useMeshStore((s) => s.snapshot?.incident ?? null);
  const detour = useMeshStore((s) => s.detour);
  const stations = useMeshStore((s) => s.stations);
  const mode = useMeshStore((s) => s.mode);
  const target = useRef<THREE.Vector3 | null>(null);

  useEffect(() => {
    if (mode === "scan") {
      target.current = lonLatToVec3(10, 22, 1).multiplyScalar(3.35);
    } else if (mode !== "incident") {
      target.current = lonLatToVec3(HOME.lon, HOME.lat, 1).multiplyScalar(HOME.dist);
    } else if (detour) {
      // Frame the corridor between its two gateways (hop midpoints skew toward dense coastlines).
      const byCode = new Map(stations.map((s) => [s.code, s]));
      const ends = [detour.hop_sequence[0], detour.hop_sequence[detour.hop_sequence.length - 1]]
        .map((c) => byCode.get(c))
        .filter(Boolean) as { lon: number; lat: number }[];
      const mid = ends.reduce((acc, p) => acc.add(lonLatToVec3(p.lon, p.lat, 1)), new THREE.Vector3());
      target.current = mid.normalize().multiplyScalar(2.5);
    } else if (incident?.fault_coordinate) {
      const { lon, lat } = incident.fault_coordinate;
      target.current = lonLatToVec3(lon, lat, 1).multiplyScalar(1.75);
    } else {
      target.current = lonLatToVec3(HOME.lon, HOME.lat, 1).multiplyScalar(HOME.dist);
    }
  }, [incident?.incident_id, incident?.fault_coordinate, detour, stations, mode]);

  useFrame((_, dt) => {
    const goal = target.current;
    if (!goal) return;
    // Slerp direction and ease radius: a heavy, hydraulic glide rather than a cut.
    const from = camera.position.clone();
    const r = THREE.MathUtils.damp(from.length(), goal.length(), 1.6, dt);
    const dir = from.normalize().lerp(goal.clone().normalize(), 1 - Math.exp(-1.6 * dt)).normalize();
    camera.position.copy(dir.multiplyScalar(r));
    camera.lookAt(0, 0, 0);
    controls.current?.update();
    if (camera.position.distanceTo(goal) < 0.01) target.current = null;
  });

  useEffect(() => {
    const c = controls.current;
    if (!c) return;
    const release = () => (target.current = null);
    c.addEventListener("start", release);
    return () => c.removeEventListener("start", release);
  }, [controls]);

  return null;
}

/**
 * Frames the globe in the free area: shifted right of the incident-flow rail, and lifted
 * above the Curator's Drawer while it is open.
 */
function ViewportFraming() {
  const { camera, size } = useThree();
  const open = useMeshStore((s) => s.drawerOpen);
  const shift = useRef({ x: 0, y: 0 });
  useFrame((_, dt) => {
    const cam = camera as THREE.PerspectiveCamera;
    // The left half of the screen is the stage for type: push the globe right, and lift it
    // above the Curator's Drawer while that is open.
    const goalX = -size.width * 0.17;
    const goalY = open ? size.height * 0.24 : size.height * 0.03;
    shift.current.x = THREE.MathUtils.damp(shift.current.x, goalX, 4, dt);
    shift.current.y = THREE.MathUtils.damp(shift.current.y, goalY, 4, dt);
    cam.setViewOffset(size.width, size.height, shift.current.x, shift.current.y, size.width, size.height);
  });
  return null;
}

export default function GlobeScene() {
  const controls = useRef<OrbitControlsImpl>(null);
  const incidentMode = useMeshStore((s) => s.mode === "incident");
  const initial = lonLatToVec3(HOME.lon, HOME.lat, HOME.dist);
  return (
    <Canvas
      className="!absolute inset-0"
      dpr={[1, 2]}
      gl={{ antialias: true, alpha: false, powerPreference: "high-performance" }}
      camera={{ position: [initial.x, initial.y, initial.z], fov: 38, near: 0.01, far: 50 }}
    >
      <color attach="background" args={["#02060F"]} />
      <BathymetryMesh />
      <CableSplines />
      {incidentMode && <DetourSpline />}
      <LandingPins />
      {incidentMode && <FaultMarker />}
      <OrbitControls
        ref={controls}
        enableDamping
        dampingFactor={0.03}
        rotateSpeed={0.35}
        zoomSpeed={0.5}
        enablePan={false}
        minDistance={1.25}
        maxDistance={4.2}
      />
      <CameraDirector controls={controls} />
      <ViewportFraming />
      <EffectComposer multisampling={0}>
        <Bloom mipmapBlur luminanceThreshold={0.35} luminanceSmoothing={0.2} intensity={0.85} radius={0.6} />
      </EffectComposer>
    </Canvas>
  );
}
