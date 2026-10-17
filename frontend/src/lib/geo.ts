import * as THREE from "three";

export const GLOBE_RADIUS = 1;

/**
 * (lon, lat) -> position on a sphere. Matches THREE.SphereGeometry's UV layout
 * (u = (lon + 180) / 360, v = (lat + 90) / 180), so equirectangular textures line up.
 */
export function lonLatToVec3(lon: number, lat: number, radius = GLOBE_RADIUS, out = new THREE.Vector3()) {
  const phi = THREE.MathUtils.degToRad(lon + 180);
  const theta = THREE.MathUtils.degToRad(90 - lat);
  return out.set(
    -radius * Math.cos(phi) * Math.sin(theta),
    radius * Math.cos(theta),
    radius * Math.sin(phi) * Math.sin(theta),
  );
}

const EARTH_KM = 6371.0088;

export function haversineKm(a: [number, number], b: [number, number]) {
  const toRad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * toRad;
  const dLon = (b[0] - a[0]) * toRad;
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * toRad) * Math.cos(b[1] * toRad) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

/**
 * Smooth a geographic polyline into a Catmull-Rom spline slightly above the seabed.
 * Long legs are pre-split along the sphere so the spline hugs the surface instead of
 * cutting a chord through the globe. Returns sampled points and cumulative km.
 */
export function polylineToSpline(
  coords: [number, number][],
  altitude: number,
  samplesPer1000Km = 14,
): { points: THREE.Vector3[]; distKm: number[] } {
  const dense: THREE.Vector3[] = [];
  const denseKm: number[] = [];
  let km = 0;
  for (let i = 0; i < coords.length; i++) {
    const p = coords[i];
    if (i > 0) {
      const prev = coords[i - 1];
      const seg = haversineKm(prev, p);
      const n = Math.max(1, Math.ceil(seg / 400));
      const a = lonLatToVec3(prev[0], prev[1], 1);
      const b = lonLatToVec3(p[0], p[1], 1);
      for (let k = 1; k <= n; k++) {
        const t = k / n;
        const v = new THREE.Vector3().copy(a).lerp(b, t).normalize().multiplyScalar(GLOBE_RADIUS + altitude);
        dense.push(v);
        denseKm.push(km + seg * t);
      }
      km += seg;
    } else {
      dense.push(lonLatToVec3(p[0], p[1], GLOBE_RADIUS + altitude));
      denseKm.push(0);
    }
  }
  if (dense.length < 2) return { points: dense, distKm: denseKm };

  const curve = new THREE.CatmullRomCurve3(dense, false, "centripetal", 0.5);
  const samples = Math.max(8, Math.ceil((km / 1000) * samplesPer1000Km) + dense.length);
  const points = curve.getSpacedPoints(samples);
  for (const v of points) v.normalize().multiplyScalar(GLOBE_RADIUS + altitude);
  const distKm = points.map((_, i) => (km * i) / samples);
  return { points, distKm };
}
