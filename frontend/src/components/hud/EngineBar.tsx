"use client";

import { useEffect, useState } from "react";
import { useMeshStore } from "@/store/useMeshStore";

const HOT_S = 1.6;

/** Short FalkorDB names; the capability description lives in the tooltip. */
const LABEL: Record<string, string> = {
  traversal: "Traversal",
  sppaths: "Shortest path (SPpaths)",
  betweenness: "Centrality (betweenness)",
  wcc: "Connected components (WCC)",
  maxflow: "maxFlow",
  copy: "GRAPH.COPY",
  mutation: "Writes",
  text2cypher: "Text to Cypher",
};

const fmtCount = (n: number) => (n >= 10_000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString());

/** The FalkorDB engine in one quiet line: what this incident has asked of it, lit while it runs. */
export default function EngineBar() {
  const engine = useMeshStore((s) => s.engine);
  const base = useMeshStore((s) => s.engineBase);
  const receivedAt = useMeshStore((s) => s.engineReceivedAt);
  const set = useMeshStore((s) => s.set);
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((t) => t + 1), 250);
    return () => clearInterval(id);
  }, []);
  const since = receivedAt ? (performance.now() - receivedAt) / 1000 : 0;
  const sessionTotal = engine?.total_calls ?? 0;
  const runTotal = Math.max(0, sessionTotal - (base?.total ?? 0));
  const scope = base?.scope ?? "this session";
  const scopeLabel = scope.charAt(0).toUpperCase() + scope.slice(1);

  return (
    <footer className="absolute inset-x-0 bottom-0 z-30 px-16 pb-[clamp(14px,2.6vh,24px)]">
      <button
        type="button"
        onClick={() => set({ drawerOpen: true })}
        title="Left: every FalkorDB query DarkGlass has run since the backend started. Right: the current run (this incident, scan, proof or question), by kind of work. The screen's own refresh queries are not counted. Click to see the queries."
        className="flex w-full flex-wrap items-baseline gap-x-5 gap-y-1 border-t border-white/[0.07] pt-3 text-left font-sans text-[12px] text-ink-3 transition-colors hover:text-ink-1"
      >
        <span className="font-medium text-ink-1">FalkorDB</span>
        <span className="tabular-nums">{sessionTotal.toLocaleString()} queries in total</span>
        <span className="h-3 w-px self-center bg-white/15" aria-hidden />
        <span className="tabular-nums">
          <span className="font-medium text-ink-1">{scopeLabel}:</span> {runTotal.toLocaleString()}
        </span>
        {(engine?.capabilities ?? []).map((c) => {
          const n = Math.max(0, c.calls - (base?.by[c.key] ?? 0));
          // Only the work that actually ran; index lookups overlap the other counts, so they are not listed.
          if (!n || c.key === "index") return null;
          const hot = c.age_s !== null && c.age_s + since < HOT_S && n > 0;
          return (
            <span
              key={c.key}
              title={`${c.name}: ${c.does}`}
              className={`transition-colors duration-300 ${hot ? "text-phosphor" : "text-ink-1"}`}
            >
              {LABEL[c.key] ?? c.name} <span className="tabular-nums">{fmtCount(n)}</span>
            </span>
          );
        })}
        <span className="ml-auto text-ink-3">View queries</span>
      </button>
    </footer>
  );
}
