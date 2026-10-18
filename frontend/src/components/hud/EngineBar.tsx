"use client";

import { useEffect, useState } from "react";
import { useMeshStore } from "@/store/useMeshStore";

const HOT_S = 1.6;

/** Short FalkorDB names; the capability description lives in the tooltip. */
const LABEL: Record<string, string> = {
  traversal: "Traversal",
  sppaths: "SPpaths",
  betweenness: "Betweenness",
  wcc: "WCC",
  maxflow: "maxFlow",
  copy: "GRAPH.COPY",
  mutation: "Mutations",
  index: "Indexes",
  text2cypher: "Text2Cypher",
};

const fmtCount = (n: number) => (n >= 10_000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString());

/** The FalkorDB engine, live: one cell per capability, lit by real query telemetry. */
export default function EngineBar() {
  const engine = useMeshStore((s) => s.engine);
  const receivedAt = useMeshStore((s) => s.engineReceivedAt);
  const set = useMeshStore((s) => s.set);
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((t) => t + 1), 250);
    return () => clearInterval(id);
  }, []);
  const since = receivedAt ? (performance.now() - receivedAt) / 1000 : 0;
  const qps = engine?.queries_per_s ?? 0;

  return (
    <footer className="absolute inset-x-0 bottom-0 z-30 px-16 pb-[clamp(16px,3.5vh,32px)]">
      <button
        type="button"
        onClick={() => set({ drawerOpen: true })}
        className="mb-[clamp(8px,1.6vh,16px)] flex items-baseline gap-3 font-sans text-[13px] text-silica/50 transition-colors hover:text-silica"
        title="Every query DarkGlass has run for cuts, agent tools, the scan, the proof and Ask since the backend started (the screen's own refresh queries are not counted). Each number below counts one kind of FalkorDB work. Click to see the queries."
      >
        <span className="font-medium text-silica">FalkorDB</span>
        <span className="tabular-nums">
          has run {(engine?.total_calls ?? 0).toLocaleString()} queries this session
        </span>
        {qps > 0 && <span className="tabular-nums text-phosphor">{qps} per second now</span>}
      </button>
      <div className="grid grid-cols-9 gap-6">
        {(engine?.capabilities ?? []).map((c) => {
          const hot = c.age_s !== null && c.age_s + since < HOT_S;
          const used = c.calls > 0;
          return (
            <div key={c.key} title={`${c.name}: ${c.does}`}>
              <div
                className={`h-[2px] rounded-full transition-all duration-300 ${
                  hot ? "bg-phosphor shadow-[0_0_14px_2px_rgba(56,189,248,0.8)]" : used ? "bg-silica/25" : "bg-white/[0.06]"
                }`}
              />
              <div className={`mt-3 font-mono text-[11px] transition-colors duration-300 ${hot ? "text-phosphor" : used ? "text-silica/70" : "text-muted/50"}`}>
                {LABEL[c.key] ?? c.name}
              </div>
              <div className={`mt-1 font-sans text-[22px] font-semibold tabular-nums tracking-[-0.02em] ${used ? "text-silica" : "text-muted/30"}`}>
                {fmtCount(c.calls)}
              </div>
            </div>
          );
        })}
      </div>
    </footer>
  );
}
