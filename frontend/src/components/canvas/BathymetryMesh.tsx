"use client";

import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { geoEquirectangular, geoGraticule10, geoPath } from "d3-geo";
import { feature } from "topojson-client";
import landTopo from "world-atlas/land-50m.json";
import { GLOBE_RADIUS } from "@/lib/geo";

const TEX_W = 4096;
const TEX_H = 2048;

/** Equirectangular land mask (R channel) and 10-degree graticule (G channel). */
function buildSurfaceTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = TEX_W;
  canvas.height = TEX_H;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, TEX_W, TEX_H);

  const projection = geoEquirectangular()
    .scale(TEX_W / (2 * Math.PI))
    .translate([TEX_W / 2, TEX_H / 2]);
  const path = geoPath(projection, ctx);

  const topo = landTopo as any;
  const land = feature(topo, topo.objects.land);
  ctx.beginPath();
  path(land as any);
  ctx.fillStyle = "#ff0000";
  ctx.fill();

  ctx.globalCompositeOperation = "lighter";
  ctx.beginPath();
  path(geoGraticule10());
  ctx.strokeStyle = "#00ff00";
  ctx.lineWidth = 0.8;
  ctx.stroke();

  const tex = new THREE.CanvasTexture(canvas);
  tex.anisotropy = 8;
  tex.colorSpace = THREE.NoColorSpace;
  return tex;
}

const vertexShader = /* glsl */ `
  uniform sampler2D uSurface;
  varying vec2 vUv;
  varying vec3 vNormalW;
  varying vec3 vViewDir;
  varying float vRelief;

  // Cheap value-noise fbm: procedural trenches and ridges (no bathymetric dataset is bundled).
  float hash(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }
  float noise(vec3 p) {
    vec3 i = floor(p); vec3 f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x), mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
               mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x), mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z);
  }
  float fbm(vec3 p) { float a = 0.5, s = 0.0; for (int i = 0; i < 5; i++) { s += a * noise(p); p *= 2.03; a *= 0.5; } return s; }

  void main() {
    vUv = uv;
    float land = texture2D(uSurface, uv).r;
    float relief = fbm(normal * 6.0);
    vRelief = relief;
    // Ocean floor sits below the cable layer; trenches dip further. Land lifts slightly.
    float displacement = mix(-0.006 - 0.010 * relief, 0.0015, land);
    vec3 pos = position + normal * displacement;
    vec4 world = modelMatrix * vec4(pos, 1.0);
    vNormalW = normalize(mat3(modelMatrix) * normal);
    vViewDir = normalize(cameraPosition - world.xyz);
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const fragmentShader = /* glsl */ `
  uniform sampler2D uSurface;
  uniform vec3 uCore;
  uniform vec3 uEdge;
  uniform vec3 uLand;
  varying vec2 vUv;
  varying vec3 vNormalW;
  varying vec3 vViewDir;
  varying float vRelief;

  void main() {
    vec4 s = texture2D(uSurface, vUv);
    float facing = clamp(dot(vNormalW, vViewDir), 0.0, 1.0);
    // Navy horizon at the centre of the disc falling to obsidian at the limb.
    vec3 ocean = mix(uEdge, uCore, pow(facing, 0.9));
    ocean *= 0.78 + 0.32 * vRelief;
    vec3 col = mix(ocean, uLand * (0.65 + 0.35 * facing), s.r);
    // Seabed grid: rgba(255,255,255,0.03)
    col = mix(col, vec3(1.0), 0.03 * s.g * (1.0 - s.r));
    gl_FragColor = vec4(col, 1.0);
  }
`;

export default function BathymetryMesh() {
  const texture = useMemo(() => buildSurfaceTexture(), []);
  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader,
        fragmentShader,
        uniforms: {
          uSurface: { value: texture },
          uCore: { value: new THREE.Color("#0A1628") },
          uEdge: { value: new THREE.Color("#02060F") },
          uLand: { value: new THREE.Color("#162438") },
        },
      }),
    [texture],
  );
  useEffect(() => () => {
    texture.dispose();
    material.dispose();
  }, [texture, material]);

  return (
    <mesh material={material} renderOrder={0}>
      <sphereGeometry args={[GLOBE_RADIUS, 256, 128]} />
    </mesh>
  );
}
