"use client";

import { useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { useMeshStore, type ScanCard } from "@/store/useMeshStore";

function Kicker({ children, tone = "text-muted" }: { children: ReactNode; tone?: string }) {
  return <div className={`font-mono text-[11px] uppercase tracking-[0.22em] ${tone}`}>{children}</div>;
}
function H1({ children }: { children: ReactNode }) {
  return (
    <h1 className="display-h1 font-sans font-semibold tracking-[-0.04em] text-silica">{children}</h1>
  );
}
function Lead({ children }: { children: ReactNode }) {
  return <p className="mt-5 max-w-[440px] font-sans text-[17px] leading-[26px] text-silica/55">{children}</p>;
}
function Action({ children, onClick, disabled }: { children: ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="mt-10 inline-flex items-center rounded-full bg-phosphor px-7 py-3.5 font-sans text-[15px] font-medium tracking-tight text-void shadow-[0_0_40px_rgba(56,189,248,0.35)] transition-shadow hover:shadow-[0_0_60px_rgba(56,189,248,0.6)] disabled:opacity-40"
    >
      {children}
    </button>
  );
}
/** Safe area between the top bar and the engine bar; content is centred vertically inside it. */
function Shell({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className={`chapter-in absolute bottom-[clamp(160px,25vh,230px)] left-[64px] top-[clamp(96px,13vh,120px)] z-20 flex flex-col justify-center ${wide ? "w-[620px]" : "w-[520px]"}`}>
      <div>{children}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ planet scan */

function Board({ title, cards, line, onPick }: {
  title: string;
  cards: ScanCard[];
  line: (c: ScanCard) => string;
  onPick: (c: ScanCard) => void;
}) {
  if (!cards.length) return null;
  return (
    <div>
      <div className="mb-2 font-sans text-[13px] text-silica/40">{title}</div>
      {cards.slice(0, 3).map((c) => (
        <button
          key={c.id}
          type="button"
          onClick={() => onPick(c)}
          className="flex w-full items-baseline justify-between gap-4 border-t border-white/[0.06] py-2.5 text-left"
          title={`Cut ${c.name} in Incident mode`}
        >
          <span className="truncate font-sans text-[15px] tracking-tight text-silica">{c.name}</span>
          <span className="shrink-0 font-sans text-[13px] tabular-nums text-silica/45">{line(c)}</span>
        </button>
      ))}
    </div>
  );
}

export function ScanPanel() {
  const st = useMeshStore();
  const { scan } = st;
  const s = scan.summary;

  const start = async () => {
    st.set({ scan: { ...scan, running: true, done: 0, total: 603, elapsedMs: 0, heat: {}, summary: null } });
    try {
      await api.runScan();
    } catch (e) {
      st.set({ notice: (e as Error).message, scan: { ...scan, running: false } });
    }
  };
  const pick = (c: ScanCard) => st.set({ selectedCable: c.id, mode: "incident" });

  if (scan.running) {
    return (
      <Shell>
        <div className="flex items-baseline gap-3 font-sans font-semibold tracking-[-0.04em]">
          <span className="text-[96px] leading-none text-silica tabular-nums">{scan.done}</span>
          <span className="text-[32px] text-silica/30">/ {scan.total || 603}</span>
        </div>
        <Lead>cables cut inside FalkorDB.</Lead>
        <div className="mt-6 h-px w-full max-w-[420px] bg-white/[0.08]">
          <div className="h-px bg-phosphor shadow-[0_0_10px_#38BDF8] transition-[width] duration-300" style={{ width: `${(100 * scan.done) / (scan.total || 603)}%` }} />
        </div>
        <div className="mt-3 font-sans text-[13px] tabular-nums text-silica/40">{(scan.elapsedMs / 1000).toFixed(1)} s</div>
      </Shell>
    );
  }

  if (s) {
    return (
      <>
        <div className="chapter-in absolute left-[64px] top-[clamp(100px,14vh,130px)] z-20 w-[560px]">
          <H1>
            {s.scenarios} cuts.
            <br />
            <span className="text-silica/35">{(s.duration_ms / 1000).toFixed(0)} seconds.</span>
          </H1>
          <Lead>Every subsea cable on Earth, failed once.</Lead>
          <button type="button" onClick={start} className="mt-6 font-sans text-[14px] text-silica/40 transition-colors hover:text-silica">
            Run again
          </button>
        </div>
        <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-[56vh] bg-gradient-to-t from-void via-void/95 to-transparent" />
        <div className="chapter-in-late absolute bottom-[clamp(150px,22vh,200px)] left-[64px] right-[64px] z-20 grid grid-cols-3 gap-12">
        <Board
          title="Cuts off countries"
          cards={s.top_planet}
          line={(c) => `${c.countries.length} countries`}
          onPick={pick}
        />
        <Board
          title="Drains the Atlantic"
          cards={s.top_capacity}
          line={(c) => `-${c.capacity_loss.toFixed(0)} Tbps`}
          onPick={pick}
        />
        <Board
          title="Freezes our GPUs"
          cards={s.top_cluster}
          line={(c) => `${c.gpus.toLocaleString()} GPUs`}
          onPick={pick}
        />
        </div>
      </>
    );
  }

  return (
    <Shell>
      <H1>
        What if every cable
        <br />
        on Earth failed?
      </H1>
      <Lead>FalkorDB cuts all 603, one by one.</Lead>
      <Action onClick={start}>Run the planet scan</Action>
    </Shell>
  );
}

/* ------------------------------------------------------------------ ask the graph */

const EXAMPLES = [
  "Which training jobs depend on cables landing at Virginia Beach?",
  "Which cables land in Ireland, and how much capacity does each have?",
  "Which five cables would cut off the most countries if they failed?",
];

export function AskPanel() {
  const st = useMeshStore();
  const [q, setQ] = useState("");
  const { loading, result, error } = st.askState;

  const submit = async (question: string) => {
    if (!question.trim() || loading) return;
    setQ(question);
    st.set({ askState: { loading: true, result: null, error: null } });
    try {
      const r = await api.ask(question);
      st.set({ askState: { loading: false, result: r, error: r.error ?? null } });
    } catch (e) {
      st.set({ askState: { loading: false, result: null, error: (e as Error).message } });
    }
  };

  const cols = result?.columns ?? [];
  return (
    <Shell wide>
      {!result && !loading ? <H1>Ask the graph.</H1> : null}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit(q);
        }}
        className="mt-7"
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Ask anything about the network"
          className="w-full border-b border-white/15 bg-transparent pb-3 font-sans text-[18px] tracking-tight text-silica placeholder:text-silica/25 focus:border-phosphor focus:outline-none"
        />
      </form>
      {!result && !loading && (
        <div className="mt-5 space-y-2">
          {EXAMPLES.map((e) => (
            <button key={e} type="button" onClick={() => submit(e)} className="block text-left font-sans text-[14px] text-silica/40 transition-colors hover:text-silica">
              {e}
            </button>
          ))}
        </div>
      )}
      {loading && (
        <div className="mt-6 font-mono text-[12px] text-phosphor">
          <span className="animate-pulse">Writing Cypher...</span>
        </div>
      )}
      {error && <div className="mt-6 font-mono text-[12px] text-crimson">{error}</div>}
      {result && !result.error && (
        <div className="mt-6">
          <div className="flex gap-6 font-sans text-[13px] tabular-nums">
            <span className="text-phosphor">FalkorDB {result.engine_ms?.toFixed(1)} ms</span>
            <span className="text-silica/40">{result.row_count} rows</span>
          </div>
          <pre
            title="scroll for the full query"
            className="mt-3 max-h-[112px] overflow-y-auto whitespace-pre-wrap rounded-lg bg-white/[0.03] p-3 font-mono text-[11px] leading-[16px] text-core/80 [mask-image:linear-gradient(to_bottom,black_72%,transparent)] hover:[mask-image:none]"
          >
            {result.cypher}
          </pre>
          <div className="mt-3 max-h-[150px] overflow-y-auto">
            <table className="w-full text-left font-sans text-[12px]">
              <thead>
                <tr className="text-muted">
                  {cols.slice(0, 4).map((c) => (
                    <th key={c} className="pb-1 pr-3 font-mono text-[10px] font-normal uppercase tracking-[0.12em]">{c.replace(/_/g, " ")}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(result.rows ?? []).slice(0, 6).map((r, i) => (
                  <tr key={i} className="border-t border-white/[0.05] text-silica/80">
                    {cols.slice(0, 4).map((c) => (
                      <td key={c} className="max-w-[160px] truncate py-1 pr-3">
                        {Array.isArray(r[c]) ? (r[c] as unknown[]).join(", ") : String(r[c] ?? "")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Shell>
  );
}

/* ------------------------------------------------------------------ proof */

const LOG_MIN = 0.1;
const LOG_MAX = 10_000;
const pct = (ms: number) =>
  Math.max(1.5, (100 * (Math.log10(Math.max(ms, LOG_MIN)) - Math.log10(LOG_MIN))) / (Math.log10(LOG_MAX) - Math.log10(LOG_MIN)));
const fmt = (ms: number) => (ms >= 100 ? `${Math.round(ms).toLocaleString()} ms` : `${ms.toFixed(ms < 10 ? 2 : 1)} ms`);

export function ProofPanel() {
  const st = useMeshStore();
  const r = st.benchmark;
  if (!r) {
    return (
      <Shell>
        <Kicker>Proof</Kicker>
        <H1>Why a graph database.</H1>
        <Lead>No benchmark on this machine yet.</Lead>
        <Action onClick={() => api.runBenchmark(true).then(() => st.set({ benchmarkRunning: true }))} disabled={st.benchmarkRunning}>
          {st.benchmarkRunning ? "Running" : "Run the benchmark"}
        </Action>
      </Shell>
    );
  }
  const sql = r.sqlite.runs[r.sqlite.runs.length - 1];
  const x = Math.round(sql.wall_ms.p50 / r.falkordb.detour_search.engine_ms.p50);
  const groups = [
    {
      title: "Safest detour",
      rows: [
        { k: "FalkorDB", v: r.falkordb.detour_search.engine_ms.p50, hero: true },
        { k: "NetworkX", v: r.networkx.detour_search.wall_ms.p50 },
        { k: "SQL", v: sql.wall_ms.p50 },
      ],
    },
    {
      title: "Bottleneck audit",
      rows: [
        { k: "FalkorDB", v: r.falkordb.centrality_sampled.engine_ms.p50, hero: true },
        { k: "NetworkX", v: r.networkx.centrality_sampled.wall_ms.p50 },
      ],
    },
  ];
  return (
    <Shell wide>
      <H1>
        <span className="text-phosphor">{x.toLocaleString()}x</span> faster
        <br />
        <span className="text-silica/40">than SQL.</span>
      </H1>
      <div className="mt-8 grid max-w-[600px] grid-cols-2 gap-10">
      {groups.map((g) => (
        <div key={g.title}>
          <div className="font-sans text-[13px] text-silica/40">{g.title}</div>
          {g.rows.map((row) => (
            <div key={row.k} className="mt-3">
              <div className="flex justify-between font-sans text-[13px]">
                <span className={row.hero ? "text-phosphor" : "text-silica/60"}>{row.k}</span>
                <span className={row.hero ? "text-phosphor" : "text-silica/80"}>{fmt(row.v)}</span>
              </div>
              <div className="mt-1.5 h-[3px] rounded-full bg-white/[0.05]">
                <div
                  className={`h-full rounded-full ${row.hero ? "bg-phosphor shadow-[0_0_12px_rgba(56,189,248,0.7)]" : "bg-[#475569]"}`}
                  style={{ width: `${pct(row.v)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      ))}
      </div>
      <p className="mt-10 font-sans text-[13px] text-silica/35">
        Same graph in every engine. Same answer. Median of {r.iterations.falkordb_search.toLocaleString()} runs.
      </p>
    </Shell>
  );
}
