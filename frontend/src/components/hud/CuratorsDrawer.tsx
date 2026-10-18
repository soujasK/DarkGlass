"use client";

import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { BenchmarkReport, CypherTrace } from "@/lib/types";
import { segments } from "@/lib/route";
import { useAgentView } from "@/store/useAgentView";
import { useMeshStore } from "@/store/useMeshStore";

/* ------------------------------------------------------------------ hop diagram */

function HopDiagram() {
  const detour = useMeshStore((s) => s.detour);
  const incident = useMeshStore((s) => s.snapshot?.incident ?? null);
  const blast = incident?.blast_radius;

  if (detour) {
    const segs = segments(detour);
    return (
      <div>
        <div className="mb-3 text-muted">DETOUR / {detour.hop_sequence.length - 1} SPANS / SRLG-ISOLATED</div>
        <div className="flex flex-wrap items-center gap-y-4">
          {segs.map((s, i) => (
            <div key={i} className="flex items-center">
              {i === 0 && <span className="text-silica">{s.from}</span>}
              <div className="mx-2 flex flex-col items-center">
                <span className={s.subsea ? "text-amber-laser" : "text-muted"}>{s.label}</span>
                <span className={`my-1 h-px w-24 ${s.subsea ? "bg-amber-laser/70" : "bg-white/20"}`} />
                <span className="text-muted">{s.latency.toFixed(2)} ms</span>
              </div>
              <span className="text-silica">{s.to}</span>
            </div>
          ))}
        </div>
        <div className="mt-4 text-silica">
          {detour.total_latency_ms?.toFixed(4)} ms one-way /{" "}
          <span className="text-muted">tau = n/c = 4.897 us/km</span>
        </div>
      </div>
    );
  }

  if (blast?.affected_tunnels) {
    const layers = [
      { k: "L1 CABLE", v: `${blast.cable_name} [${blast.srlg_violation_id}]` },
      { k: "L1 SPANS", v: (incident?.severed_spans ?? []).map((s) => s.span_id).join("  ") },
      { k: "L3 TUNNEL", v: blast.affected_tunnels.map((t: any) => t.tunnel_id).join("  ") || "none" },
      { k: "NCCL RING", v: blast.affected_rings.join("  ") || "none" },
      { k: "L7 JOB", v: blast.affected_jobs.map((j: any) => j.job_id).join("  ") || "none" },
    ];
    return (
      <div>
        <div className="mb-3 text-muted">UPWARD BLAST RADIUS / LAYER 1 TO LAYER 7</div>
        <div className="space-y-2">
          {layers.map((l, i) => (
            <div key={l.k} className="flex items-start gap-3">
              <span className="w-20 shrink-0 text-muted">{l.k}</span>
              <span className={`mt-[7px] h-px w-6 shrink-0 ${i === 0 ? "bg-crimson" : "bg-white/20"}`} />
              <span className={i === 0 ? "text-crimson" : "text-silica"}>{l.v}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }
  return <div className="text-muted">No incident. Simulate an anchor drag to trace a blast radius.</div>;
}

/* ------------------------------------------------------------------ cypher trace */

function CypherPanel() {
  const { steps } = useAgentView();
  const incident = useMeshStore((s) => s.snapshot?.incident ?? null);
  const selected = useMeshStore((s) => s.selectedTrace);
  const set = useMeshStore((s) => s.set);

  const groups = useMemo(() => {
    const g: { step: number; tool: string; traces: CypherTrace[] }[] = [];
    if (incident?.sever_trace) g.push({ step: 0, tool: "chaos_engine.sever", traces: [incident.sever_trace] });
    for (const s of steps) if (s.result?.cypher_trace) g.push({ step: s.step, tool: s.tool, traces: s.result.cypher_trace });
    return g;
  }, [steps, incident]);

  const current =
    groups.find((g) => g.step === selected?.step)?.traces[selected?.index ?? 0] ??
    groups[groups.length - 1]?.traces[0] ??
    null;

  return (
    <div className="flex h-full min-h-0 gap-4">
      <div className="w-56 shrink-0 space-y-2 overflow-y-auto pr-1">
        {groups.map((g) => (
          <div key={g.step}>
            <div className="text-muted">
              {String(g.step).padStart(2, "0")} {g.tool}
            </div>
            {g.traces.map((t, i) => {
              const active = t === current;
              return (
                <button
                  key={t.label}
                  type="button"
                  onClick={() => set({ selectedTrace: { step: g.step, index: i } })}
                  className={`flex w-full justify-between pl-3 text-left ${active ? "text-phosphor" : "text-silica/80 hover:text-silica"}`}
                >
                  <span>{t.label}</span>
                  <span>{t.engine_ms.toFixed(3)}</span>
                </button>
              );
            })}
          </div>
        ))}
        {!groups.length && <div className="text-muted">Queries appear here as the agent executes.</div>}
      </div>
      <div className="min-w-0 flex-1 overflow-y-auto">
        {current && (
          <>
            <div className="mb-2 flex flex-wrap gap-x-5 text-muted">
              <span>
                ENGINE <span className="text-phosphor">{current.engine_ms.toFixed(4)} ms</span>
              </span>
              <span>
                WALL <span className="text-silica">{current.wall_ms.toFixed(4)} ms</span>
              </span>
              <span>ROWS {current.rows}</span>
              {Object.entries(current.stats).map(([k, v]) => (
                <span key={k}>
                  {k.replace(/_/g, " ").toUpperCase()} {v}
                </span>
              ))}
            </div>
            <pre className="whitespace-pre-wrap break-words text-[11px] leading-[16px] text-core/90">{current.cypher}</pre>
            <pre className="mt-3 whitespace-pre-wrap break-words text-[10px] leading-[14px] text-muted">
              {JSON.stringify(current.params, null, 1)}
            </pre>
          </>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ benchmark */

const LOG_MIN = 0.1;
const LOG_MAX = 100_000;
const TICKS = [0.1, 1, 10, 100, 1_000, 10_000, 100_000];
const xPct = (ms: number) =>
  (100 * (Math.log10(Math.max(ms, LOG_MIN)) - Math.log10(LOG_MIN))) / (Math.log10(LOG_MAX) - Math.log10(LOG_MIN));
const fmtMs = (ms: number) =>
  ms >= 1000 ? `${ms.toLocaleString("en-US", { maximumFractionDigits: 0 })} ms` : `${ms.toFixed(ms < 10 ? 2 : 1)} ms`;

type Bar = { label: string; p50: number; detail: string; subject: boolean; timeout?: boolean };

function BarRow({ bar }: { bar: Bar }) {
  return (
    <div className="group relative flex items-center gap-3 py-[3px]">
      <span className={`w-44 shrink-0 truncate ${bar.subject ? "text-silica" : "text-muted"}`}>{bar.label}</span>
      <div className="relative h-[6px] flex-1">
        <div
          className={`absolute left-0 top-0 h-full rounded-r ${bar.subject ? "bg-phosphor" : bar.timeout ? "bg-crimson/70" : "bg-[#475569]"}`}
          style={{ width: `${xPct(bar.p50)}%` }}
        />
      </div>
      <span className="w-24 shrink-0 text-right text-silica">
        {bar.timeout ? "> " : ""}
        {fmtMs(bar.p50)}
      </span>
      <div className="pointer-events-none absolute left-48 top-full z-10 hidden whitespace-nowrap rounded border border-white/10 bg-void/95 px-2 py-1 text-silica group-hover:block">
        {bar.detail}
      </div>
    </div>
  );
}

function Axis() {
  return (
    <div className="relative ml-[188px] mr-[108px] mt-1 h-4 text-muted">
      {TICKS.map((t) => (
        <span key={t} className="absolute -translate-x-1/2" style={{ left: `${xPct(t)}%` }}>
          {t >= 1000 ? `${t / 1000}k` : t}
        </span>
      ))}
    </div>
  );
}

function BenchmarkPanel() {
  const report = useMeshStore((s) => s.benchmark);
  const running = useMeshStore((s) => s.benchmarkRunning);
  const set = useMeshStore((s) => s.set);
  const [err, setErr] = useState<string | null>(null);

  const start = async () => {
    try {
      setErr(null);
      await api.runBenchmark(true);
      set({ benchmarkRunning: true });
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  if (!report) {
    return (
      <div className="space-y-3">
        <div className="text-muted">No benchmark has been run on this machine yet.</div>
        <button type="button" onClick={start} disabled={running} className="text-phosphor disabled:text-muted">
          [{running ? "RUNNING" : "RUN BENCHMARK"}]
        </button>
        {err && <div className="text-crimson">{err}</div>}
      </div>
    );
  }
  return <BenchmarkBody report={report} running={running} onRun={start} err={err} />;
}

function BenchmarkBody({
  report: r,
  running,
  onRun,
  err,
}: {
  report: BenchmarkReport;
  running: boolean;
  onRun: () => void;
  err: string | null;
}) {
  const sqlDeep = r.sqlite.runs[r.sqlite.runs.length - 1];
  const jitterMs = useMeshStore((s) => s.health?.flagship?.jitter_ms ?? null);
  const pctLine = (p: { p50: number; p95: number; p99: number; n: number }) =>
    `p50 ${fmtMs(p.p50)}  p95 ${fmtMs(p.p95)}  p99 ${fmtMs(p.p99)}  n=${p.n}`;

  const routing: Bar[] = [
    { label: "FalkorDB SPpaths engine", p50: r.falkordb.detour_search.engine_ms.p50,
      detail: pctLine(r.falkordb.detour_search.engine_ms), subject: true },
    { label: "FalkorDB SPpaths wall", p50: r.falkordb.detour_search.wall_ms.p50,
      detail: `${pctLine(r.falkordb.detour_search.wall_ms)}  (includes Redis round trip)`, subject: true },
    { label: "NetworkX Yen k=3", p50: r.networkx.detour_search.wall_ms.p50,
      detail: pctLine(r.networkx.detour_search.wall_ms), subject: false },
    { label: `SQLite CTE ${sqlDeep.max_hops} hops`, p50: sqlDeep.wall_ms.p50, timeout: sqlDeep.status === "TIMEOUT",
      detail: sqlDeep.status === "TIMEOUT" ? `cut off at ${sqlDeep.timeout_s} s` : pctLine(sqlDeep.wall_ms), subject: false },
  ];
  const centrality: Bar[] = [
    { label: "FalkorDB betweenness, 32 src", p50: r.falkordb.centrality_sampled.engine_ms.p50,
      detail: pctLine(r.falkordb.centrality_sampled.engine_ms), subject: true },
    { label: "NetworkX betweenness, 32 src", p50: r.networkx.centrality_sampled.wall_ms.p50,
      detail: pctLine(r.networkx.centrality_sampled.wall_ms), subject: false },
    { label: "FalkorDB betweenness, exact", p50: r.falkordb.centrality_exact.engine_ms.p50,
      detail: pctLine(r.falkordb.centrality_exact.engine_ms), subject: true },
    { label: "NetworkX betweenness, exact", p50: r.networkx.centrality_exact.wall_ms.p50,
      detail: pctLine(r.networkx.centrality_exact.wall_ms), subject: false },
  ];

  return (
    <div className="space-y-3">
      <div className="text-[12px] leading-[18px]">
        <span className="text-phosphor">FalkorDB GraphBLAS: {fmtMs(r.falkordb.detour_search.engine_ms.p50)}</span>
        <span className="text-muted"> vs </span>
        <span className="text-silica">
          SQL Recursive CTE: {sqlDeep.status === "TIMEOUT" ? `> ${sqlDeep.timeout_s * 1000} ms` : fmtMs(sqlDeep.wall_ms.p50)}
        </span>
      </div>
      <div>
        <div className="text-muted">SRLG-ISOLATED DETOUR SEARCH, P50, LOG SCALE (MS)</div>
        <div className="relative">
          {jitterMs != null && (
            <div
              className="pointer-events-none absolute bottom-0 top-0 w-px bg-amber-laser/50"
              style={{ left: `calc(188px + (100% - 296px) * ${xPct(jitterMs) / 100})` }}
              title={`${jitterMs} ms collective jitter budget`}
            />
          )}
          {routing.map((b) => (
            <BarRow key={b.label} bar={b} />
          ))}
        </div>
        <Axis />
        {jitterMs != null && <div className="text-amber-laser/80">amber rule: {jitterMs} ms collective jitter budget</div>}
      </div>
      <div>
        <div className="text-muted">CHOKE-POINT AUDIT, BETWEENNESS CENTRALITY, P50 (MS)</div>
        {centrality.map((b) => (
          <BarRow key={b.label} bar={b} />
        ))}
        <Axis />
      </div>
      <table className="w-full text-left">
        <thead className="text-muted">
          <tr>
            <th className="font-normal">SQL CTE MAX HOPS</th>
            <th className="font-normal">STATUS</th>
            <th className="font-normal">P50</th>
            <th className="font-normal">BEST PATH</th>
          </tr>
        </thead>
        <tbody>
          {r.sqlite.runs.map((x) => (
            <tr key={x.max_hops} className="text-silica">
              <td>{x.max_hops}</td>
              <td className={x.status === "TIMEOUT" ? "text-crimson" : ""}>{x.status}</td>
              <td>{fmtMs(x.wall_ms.p50)}</td>
              <td className="text-muted">{x.best_path ? `${x.best_path.latency_ms} ms / ${x.best_path.hops} hops` : "none found"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="text-muted">
        PARITY {r.parity.verified ? "VERIFIED" : "MISMATCH"} sha256 {r.parity.topology_file.slice(0, 16)} /{" "}
        {r.workload.stations.toLocaleString()} N / {r.workload.spans_directed.toLocaleString()} E / best path{" "}
        {r.results_agree.falkordb_equals_networkx ? "agrees" : "DIFFERS"} ({r.results_agree.falkordb} ms)
      </div>
      <div className="flex items-center justify-between text-muted">
        <span>
          {r.mode} run {r.generated_at.replace("T", " ").replace("Z", " UTC")} / {r.duration_s} s
        </span>
        <button type="button" onClick={onRun} disabled={running} className="text-phosphor disabled:text-muted">
          [{running ? "RUNNING" : "RE-RUN QUICK"}]
        </button>
      </div>
      {err && <div className="text-crimson">{err}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ drawer */

export default function CuratorsDrawer() {
  const open = useMeshStore((s) => s.drawerOpen);
  const set = useMeshStore((s) => s.set);
  return (
    <section
      className={`absolute inset-x-4 bottom-0 z-40 h-[52vh] rounded-t-2xl border border-b-0 border-white/[0.07] bg-horizon/55 font-mono text-hud backdrop-blur-2xl transition-transform duration-700 ease-[cubic-bezier(0.22,1,0.36,1)] ${open ? "translate-y-0" : "translate-y-full"}`}
      aria-hidden={!open}
    >
      <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-3">
        <span className="font-sans text-[14px] font-semibold tracking-[-0.01em] text-silica">Under the hood</span>
        <span className="text-muted">exact Cypher, parameters and benchmarks. Every figure is measured.</span>
        <button type="button" className="text-muted hover:text-silica" onClick={() => set({ drawerOpen: false })}>
          close
        </button>
      </div>
      <div className="grid h-[calc(100%-41px)] grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)_minmax(0,1.35fr)] divide-x divide-white/[0.06]">
        <div className="overflow-y-auto p-5">
          <HopDiagram />
        </div>
        <div className="min-h-0 p-5">
          <CypherPanel />
        </div>
        <div className="overflow-y-auto p-5">
          <BenchmarkPanel />
        </div>
      </div>
    </section>
  );
}
