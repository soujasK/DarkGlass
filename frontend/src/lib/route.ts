import type { Candidate } from "./types";

export type Segment = { from: string; to: string; label: string; latency: number; subsea: boolean; hops: number };

/** Consecutive backhaul hops collapse into one terrestrial segment so the subsea crossings read clearly. */
export function segments(c: Candidate): Segment[] {
  const out: Segment[] = [];
  for (const h of c.hops) {
    const last = out[out.length - 1];
    if (h.medium === "BACKHAUL" && last && !last.subsea) {
      last.to = h.to;
      last.latency += h.latency_ms;
      last.hops += 1;
      last.label = `BACKHAUL x${last.hops}`;
    } else {
      out.push({
        from: h.from,
        to: h.to,
        label: h.medium === "SUBSEA" ? h.cable.toUpperCase() : "BACKHAUL x1",
        latency: h.latency_ms,
        subsea: h.medium === "SUBSEA",
        hops: 1,
      });
    }
  }
  return out;
}

export const usd = (v: number) =>
  `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const fmtDuration = (s: number) => (s < 1 ? `${(s * 1000).toFixed(0)} ms` : `${s.toFixed(1)} s`);
