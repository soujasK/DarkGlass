"use client";

import { useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import type { Snapshot } from "@/lib/types";
import { useMeshStore } from "@/store/useMeshStore";
import { TOOL_ORDER, placeName, useStory, type Story } from "@/store/useStory";
import CablePicker, { CableCard } from "./CablePicker";
import FocusTag from "./FocusTag";
import Roll from "./Roll";

const QUESTIONS = ["Who is hurt?", "Find a safe detour", "Check for bottlenecks", "Move the traffic"];
/** "gemini-3.8-flash" -> "Gemini 3.8 Flash" */
const prettyModel = (m?: string) =>
  (m ?? "gemini").split("-").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
const duration = (ms: number) => (ms >= 2000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);
const money = (v: number, dp = 2) =>
  `$${v.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp })}`;

const H1 = "display-h1 font-sans font-semibold tracking-[-0.04em] text-ink";
const LINE = "gap-line font-sans text-[17px] leading-[26px] text-ink-2";

function Primary({ children, onClick, tone }: { children: ReactNode; onClick: () => void; tone: "crimson" | "amber" }) {
  const cls =
    tone === "crimson"
      ? "bg-crimson text-white hover:bg-crimson/85"
      : "bg-amber-laser text-void hover:bg-amber-laser/85";
  return (
    <button
      type="button"
      onClick={onClick}
      className={`gap-action inline-flex items-center rounded-lg px-6 py-3 font-sans text-[15px] font-medium transition-colors duration-200 ${cls}`}
    >
      {children}
    </button>
  );
}

/** One line of state: a coloured dot and a plain sentence. */
function Status({ children, dot }: { children: ReactNode; dot: string }) {
  return (
    <div className="flex items-center gap-2 font-sans text-[14px] text-ink-1">
      <span className={`h-2 w-2 shrink-0 rounded-full ${dot}`} />
      {children}
    </div>
  );
}

/** A labelled fact, for the status panel. */
function Fact({ k, children }: { k: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-ink-2">{k}</dt>
      <dd className="text-ink-1">{children}</dd>
    </>
  );
}

/** Who drives the fix: the LLM choosing FalkorDB tools over MCP (default), or the deterministic playbook. */
function AgentPicker() {
  const mode = useMeshStore((st) => st.agentMode);
  const model = useMeshStore((st) => st.health?.llm_model) ?? "Gemini";
  const llmOff = useMeshStore((st) => st.health?.llm_available === false);
  const set = useMeshStore((st) => st.set);
  const opts: ["playbook" | "llm", string][] = [
    ["llm", prettyModel(model)],
    ["playbook", "Playbook"],
  ];
  return (
    <div className="mt-5 flex flex-wrap items-center gap-1 font-sans text-[13px]">
      <span className="mr-2 text-ink-3">Agent</span>
      {opts.map(([k, label]) => (
        <button
          key={k}
          type="button"
          disabled={k === "llm" && llmOff}
          title={k === "llm" && llmOff ? "Gemini is turned off on this public demo" : undefined}
          onClick={() => set({ agentMode: k })}
          className={`whitespace-nowrap rounded-full px-3 py-1 transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${mode === k ? "bg-white/[0.08] text-ink" : "text-ink-3 hover:text-ink"}`}
        >
          {label}
        </button>
      ))}
      {llmOff && <span className="mt-1.5 basis-full text-ink-3">Gemini is off on this demo; the Playbook runs the same FalkorDB tools</span>}
    </div>
  );
}

/** "Graph memory" line: what FalkorDB remembers about previous failures of this cable. */
function Memory({ history, cable }: { history: any; cable: string }) {
  if (!history?.failures) return null;
  const last = history.last;
  const via = last?.via?.length ? last.via.join(", ") : null;
  const ms = typeof last?.agent_ms === "number" ? (last.agent_ms >= 2000 ? `${(last.agent_ms / 1000).toFixed(1)} s` : `${Math.round(last.agent_ms)} ms`) : null;
  return (
    <p className="mt-3 font-sans text-[14px] text-phosphor/85">
      Graph memory: {cable} has failed {history.failures === 1 ? "once" : `${history.failures} times`} before
      {via ? `, last fixed via ${via}${ms ? ` in ${ms}` : ""}` : ""}.
    </p>
  );
}

const CLASS_LABEL: Record<string, string> = {
  GRADIENT_SYNC: "gradient sync",
  CHECKPOINT_BURST: "checkpoint traffic",
  BULK_BACKUP: "backup traffic",
};

const TOOL_LABEL: Record<string, string> = {
  trace_severed_blast_radius: "who is hurt",
  compute_optimal_optical_detour: "detour (SPpaths)",
  audit_secondary_choke_points: "bottlenecks (betweenness)",
  execute_spectrum_reallocation: "move traffic",
};

/** Markdown from the model, as plain readable text. */
const plain = (md: string) =>
  md
    .replace(/\$\\rightarrow\$/g, "→")
    .replace(/^#+\s*/gm, "")
    .replace(/\*\*|`/g, "")
    .replace(/^\s*[*-]\s+/gm, "• ")
    .trim();

/** The tools the agent called, in order, each opening its Cypher; and the model's own summary. */
function AgentTrail({ s }: { s: Story }) {
  const set = useMeshStore((st) => st.set);
  const [open, setOpen] = useState(false);
  const summary = s.result?.summary?.trim();
  if (!s.steps.length) return null;
  return (
    <div className="mt-4 max-w-[500px] font-sans text-[13px] text-ink-2">
      <FocusTag className="mr-2">Graph queries and algorithms as agent tools</FocusTag>
      <span>{s.result?.mode === "llm" ? "Tools Gemini chose:" : "Tools run:"} </span>
      {s.steps.map((step, i) => (
        <span key={step.step}>
          {i > 0 && <span className="text-ink-4"> → </span>}
          <button
            type="button"
            title="open the Cypher"
            onClick={() => set({ drawerOpen: true, selectedTrace: { step: step.step, index: 0 } })}
            className="text-phosphor/80 hover:underline"
          >
            {TOOL_LABEL[step.tool] ?? step.tool}
          </button>
          {step.result?.engine_ms != null && <span className="font-mono text-[11px]"> {step.result.engine_ms.toFixed(1)} ms</span>}
        </span>
      ))}
      {summary && (
        <div className="relative mt-2">
          <button type="button" onClick={() => setOpen((o) => !o)} className="text-ink-1 hover:text-ink">
            {open ? "Hide" : "Read"} Gemini&apos;s summary
          </button>
          {open && (
            // Floats over the stats instead of pushing the column into the header and engine bar.
            <p className="absolute left-0 top-full z-40 mt-2 max-h-[240px] w-[500px] overflow-y-auto whitespace-pre-wrap rounded-xl border border-white/[0.1] bg-[#0d1626] p-4 text-[12px] leading-[18px] text-ink-1 shadow-[0_30px_80px_rgba(0,0,0,0.85)]">
              {plain(summary)}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** Why this route: the decision explained with what the graph returned at each step. */
function why(s: Story): string | null {
  const blast = s.byStep(TOOL_ORDER[0]);
  const detour = s.byStep(TOOL_ORDER[1]);
  const audit = s.byStep(TOOL_ORDER[2]);
  const r = s.result;
  if (!r?.detour || !detour) return null;
  const parts: string[] = [];
  const sib: string[] = blast?.srlg_exposure?.cable_names ?? [];
  if (sib.length) parts.push(`${sib.join(", ")} ${sib.length === 1 ? "shares" : "share"} the damaged trench`);
  const full = detour.masked_spans?.capacity;
  if (full) parts.push(`${full.toLocaleString()} links were too full`);
  if (r.detour_latency_ms != null && r.ceiling_ms != null)
    parts.push(`${r.detour_latency_ms.toFixed(1)} ms fits the ${r.ceiling_ms.toFixed(1)} ms limit`);
  if (audit?.highest_centrality_station)
    parts.push(`the busiest relay (${placeName(audit.highest_centrality_station, s.byCode)}) is ${audit.risk_assessment === "CRITICAL" ? "a bottleneck" : "within limits"}`);
  if (!parts.length) return null;
  const last = parts.pop();
  return `Why this route: ${parts.length ? `${parts.join(", ")}, and ${last}` : last}.`;
}

/** One-line answer per question; the full result is a click away in the drawer. */
function answer(i: number, s: Story): string {
  const r = s.byStep(TOOL_ORDER[i]);
  if (!r) return "";
  switch (i) {
    case 0:
      if (!r.affected_jobs?.[0]) return "No job affected";
      return r.impact?.servers
        ? `${r.traversal?.hops ?? 11} hops to ${r.impact.servers.toLocaleString()} servers`
        : `${r.traversal?.hops ?? 11}-hop walk, ${s.gpus.toLocaleString()} GPUs`;
    case 1: {
      const x = (r.hops ?? []).find((h: any) => h.medium === "SUBSEA");
      return r.path_found && x ? `${s.cableName(x.cable)}, ${r.total_latency_ms?.toFixed(1)} ms` : "None in budget";
    }
    case 2:
      return r.highest_centrality_station ? `${r.stations?.length ?? 0} stations clear` : "Clear";
    case 3:
      return `${r.roadm_configuration_payload?.slice_tbps} Tbps reserved`;
    default:
      return "";
  }
}

function Reasoning({ s }: { s: Story }) {
  const set = useMeshStore((st) => st.set);
  return (
    <ol className="mt-10 w-[440px]">
      {QUESTIONS.map((q, i) => {
        const step = s.steps.find((x) => x.tool === TOOL_ORDER[i]);
        const r = step?.result;
        const live = !!step && !r;
        return (
          <li
            key={q}
            className={`flex items-baseline justify-between border-t border-white/[0.06] py-3.5 transition-opacity duration-700 ${
              step ? "opacity-100" : "opacity-100"
            }`}
          >
            <span className="font-sans text-[15px] tracking-tight text-ink">{q}</span>
            <span className="flex items-baseline gap-4">
              <span className="font-sans text-[13px] text-ink-2">{r ? answer(i, s) : ""}</span>
              {r ? (
                <button
                  type="button"
                  title="open the Cypher"
                  onClick={() => set({ drawerOpen: true, selectedTrace: { step: step!.step, index: 0 } })}
                  className="w-[64px] text-right font-mono text-[11px] text-phosphor hover:underline"
                >
                  {r.engine_ms?.toFixed(1)} ms
                </button>
              ) : (
                <span className={`w-[64px] text-right font-mono text-[11px] ${live ? "animate-pulse text-phosphor" : ""}`}>
                  {live ? "..." : ""}
                </span>
              )}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export default function Chapter() {
  const s = useStory();
  const st = useMeshStore();
  const { phase, inc, result } = s;

  const guard = async (fn: () => Promise<void>) => {
    try {
      st.set({ notice: null });
      await fn();
    } catch (e) {
      st.set({ notice: (e as Error).message });
    }
  };
  const cut = () =>
    guard(async () => {
      st.clearRun();
      st.setSnapshot(await api.anchorDrag(st.selectedCable));
    });
  const fix = () =>
    guard(async () => {
      // Switch to the reasoning view immediately; the run's own events then fill it in.
      st.set({ agentRunning: true, steps: [], agentResult: null });
      try {
        const res = await api.runAgent(st.agentMode);
        if ("server_time" in res) st.setSnapshot(res as Snapshot);
      } catch (e) {
        st.set({ agentRunning: false });
        throw e;
      }
    });

  /** One click: restore the network, then cut the chosen cable. */
  const nextScenario = (id: string) =>
    guard(async () => {
      st.clearRun();
      st.setSnapshot(await api.reset());
      st.set({ selectedCable: id });
      st.setSnapshot(await api.anchorDrag(id));
      const [tunnels, health] = await Promise.all([api.tunnels(), api.health()]);
      st.set({ tunnels, health });
    });

  const target = st.catalog.find((c) => c.id === st.selectedCable) ?? st.faultCables.find((c) => c.id === st.selectedCable);
  const flagship = st.health?.flagship ?? null;
  const syncTunnels = st.tunnels.filter((t) => t.traffic_class === "GRADIENT_SYNC");

  const tryAnother = (
    <div className="gap-section">
      <div>
        <CablePicker value={null} exclude={inc?.cable_id} label="Cut a different cable" onChange={(id) => nextScenario(id)} />
      </div>
    </div>
  );

  let body: ReactNode = null;

  if (phase === "idle") {
    body = (
      <>
        <Status dot="bg-[#34d399]">All training links healthy</Status>
        <h1 className={`mt-3 ${H1}`}>{flagship?.name ?? "Training job"}</h1>
        <dl className="mt-6 grid grid-cols-[132px_1fr] gap-x-4 gap-y-2 font-sans text-[15px]">
          {flagship && <Fact k="GPUs">{flagship.gpus.toLocaleString()} across Ashburn (IAD) and Dublin (DUB)</Fact>}
          {flagship && <Fact k="Gradient sync">every {Math.round(flagship.sync_ms)} ms, {Math.round(flagship.jitter_ms)} ms jitter budget</Fact>}
          {syncTunnels.length > 0 && (
            <Fact k="Sync route">
              {syncTunnels.length} tunnels, {Math.round(syncTunnels[0].current_latency_ms ?? syncTunnels[0].baseline_latency_ms)} ms one way,
              over {new Set(syncTunnels.flatMap((t) => t.carried_by)).size} subsea cables
            </Fact>
          )}
        </dl>
        <p className="mt-3 font-sans text-[13px] text-ink-3">A modelled cluster on the real TeleGeography cable map.</p>

        <div className="gap-section">
          <div>
            <CablePicker value={st.selectedCable} onChange={(id) => st.set({ selectedCable: id })} />
          </div>
          <CableCard id={st.selectedCable} />
        </div>
        <Primary tone="crimson" onClick={cut}>
          Cut {target?.name ?? "a cable"}
        </Primary>
      </>
    );
  } else if (phase === "cut" && inc) {
    body = s.stalls ? (
      <>
        <Status dot="bg-crimson">{inc.cable_name} is cut. Gradient sync has stalled.</Status>
        <div className="mt-6 font-sans text-[13px] text-ink-2">Estimated cost so far</div>
        <div className="display-money mt-1 font-sans font-semibold tracking-[-0.04em] text-crimson">
          <Roll text={money(s.lossUsd)} />
        </div>
        <p className={LINE}>
          {s.gpus.toLocaleString()} GPUs frozen.{" "}
          <span className="text-ink-3">Cost at an estimated {money(s.ratePerSec * 3600, 0)} an hour.</span>
        </p>
        <Memory history={inc.blast_radius?.history} cable={inc.cable_name} />
        <Primary tone="amber" onClick={fix}>
          Let FalkorDB fix it
        </Primary>
        <AgentPicker />
      </>
    ) : (
      <>
        <Status dot="bg-crimson">{inc.cable_name} is cut.</Status>
        <h1 className={`mt-3 ${H1}`}>No GPUs frozen</h1>
        <p className={LINE}>
          {inc.blast_radius?.affected_tunnels?.length
            ? `${[...new Set(inc.blast_radius.affected_tunnels.map((t: any) => CLASS_LABEL[t.traffic_class] ?? t.traffic_class))]
                .join(" and ")
                .replace(/^./, (c: string) => c.toUpperCase())} needs a new path.`
            : (() => {
                const meta = st.catalog.find((c) => c.id === inc.cable_id);
                if (meta?.isolates.length) return `But it isolates stations in ${meta.isolates.join(", ")}.`;
                if (meta?.atlantic_loss_tbps) return `But it removes ${Math.round(meta.atlantic_loss_tbps)} Tbps of transatlantic capacity.`;
                return "None of our workloads depend on it.";
              })()}
        </p>
        {inc.blast_radius?.affected_tunnels?.length ? (
          <>
            <Primary tone="amber" onClick={fix}>
              Let FalkorDB reroute it
            </Primary>
            <AgentPicker />
          </>
        ) : (
          tryAnother
        )}
      </>
    );
  } else if (phase === "thinking") {
    const next = TOOL_ORDER.findIndex((t) => !s.byStep(t));
    body = (
      <>
        <Status dot="bg-phosphor animate-pulse">
          {st.agentMode === "llm" ? `${prettyModel(st.health?.llm_model)} is choosing tools over MCP` : "The playbook is running the four tools"}
          {st.agentMode === "llm" && <FocusTag className="ml-1">Tool selection through MCP</FocusTag>}
        </Status>
        <h1 className={`mt-3 min-h-[2.1em] ${H1}`}>{QUESTIONS[next === -1 ? 3 : next]}</h1>
        <div className="mt-8">
          <FocusTag>Graph queries and algorithms as agent tools</FocusTag>
        </div>
        <Reasoning s={s} />
      </>
    );
  } else if (phase === "fixed" && result) {
    const crossing = result.detour?.hops.find((h) => h.medium === "SUBSEA");
    const delta = result.latency_delta_ms ?? 0;
    // This very cut, measured live in FalkorDB, SQL and NetworkX (see Proof).
    const live = st.proof.report?.cable_id === inc?.cable_id ? st.proof.report : null;
    const measuring = !live && st.proof.running && st.proof.cableId === inc?.cable_id;
    const vsSql: [string, string] = live
      ? [`${live.speedup.vs_sqlite_is_lower_bound ? ">" : ""}${Math.round(live.speedup.vs_sqlite).toLocaleString()}x`, "faster than SQL, this cut"]
      : measuring
        ? ["—", "vs SQL, measuring now"]
        : [String(s.steps.length), s.steps.length === 1 ? "agent tool run" : "agent tools run"];
    const stats: [string, string][] = [
      s.stalls ? [(result.restored_gpus ?? s.gpus).toLocaleString(), "GPUs back"] : [`${result.spectrum_tbps} Tbps`, "rerouted"],
      [`${result.detour_latency_ms?.toFixed(1)} ms`, "new route latency"],
      vsSql,
    ];
    body = (
      <>
        <Status dot="bg-amber-laser">{s.stalls ? "Training restored" : "Traffic rerouted"}</Status>
        <h1 className={`mt-3 ${H1}`}>Fixed in {duration(result.agent_wall_ms)}</h1>
        {crossing && (
          <p className={LINE}>
            <FocusTag className="mr-2">Actions based on what the graph shows</FocusTag>
            Now on {s.cableName(crossing.cable)}, {delta <= 0 ? `${Math.abs(delta).toFixed(1)} ms faster` : `+${delta.toFixed(1)} ms`}.
            {result.mode === "llm" && (
              <span className="text-ink-3">
                {" "}Decided by {prettyModel(st.health?.llm_model)} over MCP; FalkorDB did the work in {Math.round(result.graph_engine_ms)} ms.
              </span>
            )}
          </p>
        )}
        {why(s) && (
          <p className="mt-3 max-w-[480px] font-sans text-[14px] leading-[21px] text-ink-2">
            <FocusTag className="mr-2">Explainable results</FocusTag>
            {why(s)}
          </p>
        )}
        {!!result.still_down?.length && (
          <p className="mt-3 max-w-[480px] font-sans text-[14px] leading-[21px] text-crimson/80">
            Not fixed yet: {[...new Set(result.still_down.map((t) => CLASS_LABEL[t.traffic_class] ?? t.traffic_class))].join(" and ")}{" "}
            {result.still_down.length === 1 ? "is" : "are"} still on the cut cable. The agent moves the most critical traffic first.
          </p>
        )}
        <p className="mt-3 font-sans text-[13px] text-ink-3">Saved in the graph: reload and the incident is still here.</p>
        <AgentTrail s={s} />
        <div className="gap-section flex gap-14">
          {stats.map(([v, k]) => (
            <div key={k}>
              <div className="font-sans text-[30px] font-semibold tracking-[-0.03em] text-ink">{v}</div>
              <div className="mt-1 font-sans text-[13px] text-ink-3">{k}</div>
            </div>
          ))}
        </div>
        {tryAnother}
      </>
    );
  } else if (phase === "failed") {
    // Say what actually stopped the fix: only a failed route search means "no safe detour".
    const reason = result?.reason ?? "";
    const noRoute = /^no SRLG-isolated path/.test(reason);
    body = (
      <>
        <div className="font-sans text-[17px] text-crimson">{noRoute ? "No safe detour." : "Not fixed."}</div>
        <h1 className={`mt-3 ${H1}`}>{noRoute ? "Nothing fits the budget" : "The agent stopped"}</h1>
        {reason && <p className={LINE}>{reason.charAt(0).toUpperCase() + reason.slice(1)}.</p>}
        {!noRoute && (
          <>
            <Primary tone="amber" onClick={fix}>
              Try again
            </Primary>
            <AgentPicker />
          </>
        )}
        {tryAnother}
      </>
    );
  }

  return (
    <div className="absolute bottom-[clamp(64px,9vh,96px)] left-[64px] top-[clamp(96px,13vh,120px)] z-40 flex w-[520px] flex-col justify-center">
      <div key={phase} className="chapter-in">
        {body}
      </div>
      {st.notice && <div className="mt-4 font-mono text-[11px] text-crimson">{st.notice}</div>}
    </div>
  );
}
