"use client";

import { useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { engineBase, useMeshStore, type ScanCard } from "@/store/useMeshStore";
import CablePicker from "./CablePicker";

function Kicker({ children, tone = "text-muted" }: { children: ReactNode; tone?: string }) {
  return <div className={`font-sans text-[14px] ${tone}`}>{children}</div>;
}
function H1({ children }: { children: ReactNode }) {
  return (
    <h1 className="display-h1 font-sans font-semibold tracking-[-0.04em] text-ink">{children}</h1>
  );
}
function Lead({ children }: { children: ReactNode }) {
  return <p className="mt-5 max-w-[440px] font-sans text-[17px] leading-[26px] text-ink-2">{children}</p>;
}
function Action({ children, onClick, disabled }: { children: ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="mt-10 inline-flex items-center rounded-lg bg-phosphor px-6 py-3 font-sans text-[15px] font-medium text-void transition-colors hover:bg-phosphor/85 disabled:opacity-40"
    >
      {children}
    </button>
  );
}
/** Safe area between the top bar and the engine bar; content is centred vertically inside it. */
function Shell({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className={`chapter-in absolute bottom-[clamp(72px,10vh,104px)] left-[64px] top-[clamp(96px,13vh,120px)] z-40 flex flex-col justify-center ${wide ? "w-[620px]" : "w-[520px]"}`}>
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
      <div className="mb-2 font-sans text-[13px] text-ink-3">{title}</div>
      {cards.slice(0, 3).map((c) => (
        <button
          key={c.id}
          type="button"
          onClick={() => onPick(c)}
          className="flex w-full items-baseline justify-between gap-4 border-t border-white/[0.06] py-2.5 text-left"
          title={`Cut ${c.name} in Incident mode`}
        >
          <span className="truncate font-sans text-[15px] tracking-tight text-ink">{c.name}</span>
          <span className="shrink-0 font-sans text-[13px] tabular-nums text-ink-2">{line(c)}</span>
        </button>
      ))}
    </div>
  );
}

/** The scan never touches the live graph: say so, with the real sandbox count. */
function Sandboxes({ n }: { n: number }) {
  return (
    <p className="mt-3 max-w-[480px] font-sans text-[14px] leading-[21px] text-ink-2">
      {n} sandbox graphs in one FalkorDB instance (GRAPH.COPY); the live graph is never touched.
    </p>
  );
}

export function ScanPanel() {
  const st = useMeshStore();
  const { scan } = st;
  const s = scan.summary;

  const start = async () => {
    // Count from the click, so the scan's GRAPH.COPY sandboxes are included in "this scan".
    st.set({ scan: { ...scan, running: true, done: 0, total: st.catalog.length, elapsedMs: 0, heat: {}, summary: null },
             ...(st.engine ? { engineBase: engineBase(st.engine, "this scan") } : {}) });
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
          <span className="text-[96px] leading-none text-ink tabular-nums">{scan.done}</span>
          <span className="text-[32px] text-ink-3">/ {scan.total || st.catalog.length}</span>
        </div>
        <Lead>cables cut inside FalkorDB.</Lead>
        <div className="mt-6 h-px w-full max-w-[420px] bg-white/[0.08]">
          <div className="h-px bg-phosphor transition-[width] duration-300" style={{ width: `${(100 * scan.done) / (scan.total || st.catalog.length)}%` }} />
        </div>
        <div className="mt-3 font-sans text-[13px] tabular-nums text-ink-3">{(scan.elapsedMs / 1000).toFixed(1)} s</div>
        {scan.workers ? <Sandboxes n={scan.workers} /> : null}
      </Shell>
    );
  }

  if (s) {
    return (
      <>
        <div className="chapter-in absolute left-[64px] top-[clamp(100px,14vh,130px)] z-20 w-[560px]">
          <H1>
            {s.scenarios} cables cut
            <br />
            <span className="text-ink-3">in {(s.duration_ms / 1000).toFixed(0)} s</span>
          </H1>
          <Lead>Every subsea cable on Earth, failed once.</Lead>
          <Sandboxes n={s.workers} />
          <button type="button" onClick={start} className="mt-6 font-sans text-[14px] text-ink-3 transition-colors hover:text-ink">
            Run again
          </button>
        </div>
        <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-[56vh] bg-gradient-to-t from-void via-void/95 to-transparent" />
        <div className="chapter-in-late absolute bottom-[clamp(64px,9vh,96px)] left-[64px] right-[64px] z-20 grid grid-cols-3 gap-12">
        <Board
          title="Strands landing stations"
          cards={s.top_planet}
          line={(c) => `${c.isolated} in ${c.countries.length} ${c.countries.length === 1 ? "country" : "countries"}`}
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
      <Lead>FalkorDB cuts all {st.catalog.length ? st.catalog.length.toLocaleString() : "of them"}, one by one.</Lead>
      <Action onClick={start}>Run the planet scan</Action>
    </Shell>
  );
}

/* ------------------------------------------------------------------ ask the graph */

const SHOWN_ROWS = 6;

const EXAMPLES = [
  "Which training jobs depend on cables landing at Virginia Beach?",
  "Which cables land in Ireland, and how much capacity does each have?",
  "Which five cables would strand the most landing stations if they failed?",
];

export function AskPanel() {
  const st = useMeshStore();
  const [q, setQ] = useState("");
  const { loading, result, error } = st.askState;

  const submit = async (question: string) => {
    if (!question.trim() || loading) return;
    setQ(question);
    st.set({ askState: { loading: true, result: null, error: null },
             ...(st.engine ? { engineBase: engineBase(st.engine, "this question") } : {}) });
    try {
      const r = await api.ask(question);
      st.set({ askState: { loading: false, result: r, error: r.error ?? null } });
    } catch (e) {
      st.set({ askState: { loading: false, result: null, error: (e as Error).message } });
    }
  };

  const cols = result?.columns ?? [];
  if (st.health?.llm_available === false) {
    return (
      <Shell wide>
        <H1>Ask the graph</H1>
        <Lead>
          Ask the graph turns a plain-English question into read-only Cypher with Gemini. Gemini is turned off on this
          public demo so visitors cannot spend its budget. Run DarkGlass locally with your own Vertex AI project to try it,
          or watch it in the demo video.
        </Lead>
      </Shell>
    );
  }
  return (
    <Shell wide>
      {!result && !loading ? <H1>Ask the graph</H1> : null}
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
          className="w-full border-b border-white/15 bg-transparent pb-3 font-sans text-[18px] tracking-tight text-ink placeholder:text-ink-4 focus:border-phosphor focus:outline-none"
        />
      </form>
      {!result && !loading && (
        <div className="mt-5 space-y-2">
          {EXAMPLES.map((e) => (
            <button key={e} type="button" onClick={() => submit(e)} className="block text-left font-sans text-[14px] text-ink-3 transition-colors hover:text-ink">
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
            <span className="text-ink-3">
              {result.row_count} {result.row_count === 1 ? "row" : "rows"}
              {(result.row_count ?? 0) > SHOWN_ROWS ? `, first ${SHOWN_ROWS} shown` : ""}
            </span>
          </div>
          <pre
            title="scroll for the full query"
            className="mt-3 max-h-[112px] overflow-y-auto whitespace-pre-wrap rounded-lg bg-white/[0.03] p-3 font-mono text-[11px] leading-[16px] text-core/80 [mask-image:linear-gradient(to_bottom,black_72%,transparent)] hover:[mask-image:none]"
          >
            {result.cypher}
          </pre>
          {result.row_count === 0 && (
            <p className="mt-3 font-sans text-[13px] text-ink-2">
              Nothing in the graph matches right now. The answer reflects the live graph, including any cut or reroute.
            </p>
          )}
          <div className="mt-3 max-h-[150px] overflow-y-auto">
            <table className="w-full text-left font-sans text-[12px]">
              <thead>
                <tr className="text-muted">
                  {cols.slice(0, 4).map((c) => (
                    <th key={c} className="pb-1 pr-3 font-sans text-[12px] font-normal">{c.replace(/_/g, " ").replace(/^./, (x) => x.toUpperCase())}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(result.rows ?? []).slice(0, SHOWN_ROWS).map((r, i) => (
                  <tr key={i} className="border-t border-white/[0.05] text-ink-1">
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
  Math.max(1.5, Math.min(100, (100 * (Math.log10(Math.max(ms, LOG_MIN)) - Math.log10(LOG_MIN))) / (Math.log10(LOG_MAX) - Math.log10(LOG_MIN))));
const fmt = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : ms >= 100 ? `${Math.round(ms)} ms` : `${ms.toFixed(ms < 10 ? 2 : 1)} ms`);

const STAGES: [string, string][] = [
  ["snapshot", "GRAPH.COPY and cut"],
  ["falkordb", "FalkorDB"],
  ["export", "Same snapshot out"],
  ["sqlite", "SQL"],
  ["networkx", "NetworkX"],
];

type Row = { k: string; ms: number; hero?: boolean; timeout?: boolean; agree?: boolean | null };

function Bars({ title, rows, answer, note }: { title: string; rows: Row[]; answer: string; note?: string }) {
  return (
    <div>
      <div className="font-sans text-[13px] text-ink-3">{title}</div>
      {rows.map((row) => (
        <div key={row.k} className="mt-3">
          <div className="flex justify-between font-sans text-[13px] tabular-nums">
            <span className={row.hero ? "text-phosphor" : "text-ink-1"}>{row.k}</span>
            <span className={row.hero ? "text-phosphor" : row.timeout ? "text-crimson/80" : "text-ink-1"}>
              {row.timeout ? `> ${fmt(row.ms)}, gave up` : fmt(row.ms)}
              {row.agree === false && <span className="text-crimson"> / different answer</span>}
            </span>
          </div>
          <div className="mt-1.5 h-[3px] rounded-full bg-white/[0.05]">
            <div
              className={`h-full rounded-full ${row.hero ? "bg-phosphor" : row.timeout ? "bg-crimson/50" : "bg-[#F2F4F7]"}`}
              style={{ width: `${pct(row.ms)}%` }}
            />
          </div>
        </div>
      ))}
      <div className="mt-4 font-sans text-[13px] text-ink-1">{answer}</div>
      {note && <div className="mt-1 font-sans text-[12px] leading-[18px] text-ink-3">{note}</div>}
    </div>
  );
}

export function ProofPanel() {
  const st = useMeshStore();
  const { proof } = st;
  const r = proof.report;
  const incCable = st.snapshot?.incident?.cable_id ?? null;

  const start = async (id?: string) => {
    st.set({ notice: null, proof: { ...proof, running: true, stage: "snapshot", cableId: id ?? incCable, error: null } });
    try {
      await api.runProof(id);
    } catch (e) {
      st.set({ notice: (e as Error).message, proof: { ...useMeshStore.getState().proof, running: false, stage: null } });
    }
  };

  const picker = (
    <div className="mt-8 w-[420px]">
      <CablePicker value={null} label={r ? "Prove it on another cable" : "Pick a cable to prove it on"} onChange={(id) => start(id)} />
    </div>
  );

  if (proof.running) {
    const at = STAGES.findIndex(([k]) => k === proof.stage);
    const name = st.catalog.find((c) => c.id === proof.cableId)?.name ?? proof.cableId;
    return (
      <Shell wide>
        <Kicker tone="text-ink-1">Live proof</Kicker>
        <H1>
          Measuring the
          <br />
          <span className="text-ink-3">{name} cut</span>
        </H1>
        <ol className="mt-8 space-y-2 font-sans text-[14px]">
          {STAGES.map(([k, label], i) => (
            <li key={k} className={i < at ? "text-ink-2" : i === at ? "text-phosphor" : "text-ink-4"}>
              {label}
            </li>
          ))}
        </ol>
      </Shell>
    );
  }

  if (!r) {
    return (
      <Shell>
        <Kicker tone="text-ink-1">Proof</Kicker>
        <H1>Why a graph database</H1>
        <Lead>Pick any cable. FalkorDB cuts it in a sandbox copy, then SQL and NetworkX answer the agent&apos;s questions on the same snapshot.</Lead>
        {incCable ? <Action onClick={() => start()}>Prove it on this cut</Action> : picker}
      </Shell>
    );
  }

  const e = r.engines;
  const q = r.questions;
  const imp = q.hurt.answer.falkordb;
  const lower = r.speedup.vs_sqlite_is_lower_bound;
  const hurtRows: Row[] = [
    { k: "FalkorDB", ms: e.falkordb.hurt_ms, hero: true },
    { k: "NetworkX", ms: e.networkx.hurt_ms, agree: q.hurt.agree.networkx },
    { k: "SQL", ms: e.sqlite.hurt_ms, timeout: e.sqlite.hurt_timed_out, agree: q.hurt.agree.sqlite },
  ];
  const detourRows: Row[] = [
    { k: "FalkorDB", ms: e.falkordb.detour_ms, hero: true },
    { k: "NetworkX", ms: e.networkx.detour_ms, agree: q.detour.agree.networkx },
    { k: "SQL", ms: e.sqlite.detour_ms, timeout: e.sqlite.detour_timed_out, agree: q.detour.agree.sqlite },
  ];
  const route = q.detour.answer.falkordb;
  const hinted = e.sqlite.detour_hinted_ms;
  return (
    <Shell wide>
      <Kicker tone="text-ink-1">Live proof on the {r.cable_name} cut</Kicker>
      <H1>
        {lower ? "Over " : ""}{Math.round(r.speedup.vs_sqlite).toLocaleString()}x faster
        <br />
        <span className="text-ink-3">than SQL on this cut</span>
      </H1>
      <div className="mt-8 grid max-w-[600px] grid-cols-2 gap-10">
        <Bars
          title="Who is hurt"
          rows={hurtRows}
          answer={imp.gpus ? `${imp.gpus.toLocaleString()} GPUs, ${imp.racks.toLocaleString()} racks, ${imp.hops} hops` : "No workload on this cable"}
        />
        <Bars
          title="Safest detour"
          rows={detourRows}
          answer={route != null ? `${q.detour.inputs.src} to ${q.detour.inputs.dst}, ${route.toFixed(1)} ms, ${q.detour.answer.hops} hops` : "No route in budget"}
          note={e.sqlite.detour_timed_out && hinted != null
            ? `SQL cannot know the best route is ${e.sqlite.detour_hinted_hops} hops deep. Told that, it takes ${fmt(hinted)}.`
            : undefined}
        />
      </div>
      <p className="mt-8 max-w-[600px] font-sans text-[13px] leading-[20px] text-ink-3">
        Same snapshot of {r.snapshot.edges.toLocaleString()} edges in all three{r.parity.verified ? `, sha256 ${r.parity.falkordb.slice(0, 8)} verified` : ""}.
        {" "}Wall-clock, median of {r.runs.falkordb}. Answers match{lower ? " where SQL finished" : ""}.
      </p>
      {picker}
    </Shell>
  );
}
