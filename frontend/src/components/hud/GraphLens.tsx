"use client";

import { useMeshStore } from "@/store/useMeshStore";
import FocusTag from "./FocusTag";
import { TOOL_ORDER, useStory } from "@/store/useStory";

const W = 700;
const H = 196;
const JOB = { x: 92, y: 98 };
const TUN = { x: 372, y: 98 };
const CX = 560;
const CUT = { x: CX, y: 40 };
const SIB = { x: CX, y: 98 };
const NEW = { x: CX, y: 156 };

const CYAN = "#7AA7D9";
const AMBER = "#E0B156";
const CRIMSON = "#E5534B";
const DIM = "#334155";

const CLASS_SHORT: Record<string, string> = {
  GRADIENT_SYNC: "training sync",
  CHECKPOINT_BURST: "checkpoints",
  BULK_BACKUP: "backups",
};

function Edge({ a, b, color, mode, width = 1 }: {
  a: { x: number; y: number };
  b: { x: number; y: number };
  color: string;
  mode: "solid" | "dashed" | "flow" | "trace";
  width?: number;
}) {
  const cls = mode === "flow" ? "lens-flow" : mode === "trace" ? "lens-trace" : "";
  return (
    <line
      x1={a.x} y1={a.y} x2={b.x} y2={b.y}
      stroke={color}
      strokeWidth={width}
      strokeDasharray={mode === "dashed" ? "3 4" : mode === "flow" || mode === "trace" ? "6 6" : undefined}
      className={`${cls} transition-[stroke] duration-700`}
      strokeLinecap="round"
    />
  );
}

/**
 * The FalkorDB subgraph this incident touches, drawn live:
 * TrainingJob -[:COORDINATES]-> NCCL_Ring -[:USES_TUNNEL]-> LogicalTunnel -[:CARRIED_BY]-> CableSystem.
 */
export default function GraphLens() {
  const s = useStory();
  const faultCables = useMeshStore((st) => st.faultCables);
  const selected = useMeshStore((st) => st.selectedCable);
  const { phase, inc } = s;

  const done = (i: number) => !!s.byStep(TOOL_ORDER[i]);
  const traced = done(0);
  const routed = done(1);
  const audited = done(2);
  const applied = phase === "fixed";

  const blast = inc?.blast_radius;
  const traversal = s.byStep(TOOL_ORDER[0])?.traversal ?? null;
  const impact = s.byStep(TOOL_ORDER[0])?.impact ?? null;
  const job = blast?.affected_jobs?.[0];
  const tunnel = blast?.affected_tunnels?.[0];
  const ringCount = Math.max(1, Math.min(8, blast?.affected_rings?.length ?? 8));
  const detour = s.byStep(TOOL_ORDER[1]);
  const crossing = detour?.hops?.find((h: any) => h.medium === "SUBSEA");
  const sibling: string | undefined = blast?.srlg_exposure?.cable_names?.[0];
  const cutName = inc?.cable_name ?? faultCables.find((c) => c.id === selected)?.name ?? "Dunant";
  const noTraffic = !!inc && !tunnel;

  const rings = Array.from({ length: ringCount }, (_, i) => ({
    x: 232,
    y: 98 + (i - (ringCount - 1) / 2) * Math.min(22, 120 / ringCount),
  }));

  // Colours by stage
  const hurt = traced && !applied;
  const upColor = applied ? AMBER : hurt ? CRIMSON : CYAN;
  const nodeStroke = applied ? AMBER : hurt ? CRIMSON : CYAN;

  let caption = "Every GPU job is linked to the undersea glass it depends on.";
  if (phase === "cut") caption = noTraffic ? "No tunnel in the graph uses this cable." : "The cut is written to the graph. Nothing upstream knows yet.";
  if (phase === "thinking" && !traced) caption = "Walking up from the cut cable to everything that depends on it.";
  if (traced && !routed) caption = "One traversal from the seabed to the GPU. The damage is mapped.";
  if (routed && !applied) caption = `Detour found. ${sibling ?? "The shared-trench cable"} was masked out before the search.`;
  if (audited && !applied) caption = "Every relay station on the detour scored for overload risk.";
  if (applied) caption = "The tunnel's CARRIED_BY edge now points at the new cable.";

  // Nothing to draw before a cut, or when no workload in the graph depends on the cut cable.
  if (phase === "idle" || noTraffic) return null;

  return (
    <div className="chapter-in-late pointer-events-none absolute bottom-[clamp(56px,8vh,84px)] right-[48px] z-20 w-[clamp(480px,37vw,600px)] rounded-xl border border-white/[0.08] bg-[#070d19]/[0.92] px-5 pb-4 pt-5">
      {traced && traversal && (
        <div className="chapter-in mb-4">
          <div className="font-sans text-[13px] font-medium text-ink-1">
            {traversal.hops}-hop traversal in FalkorDB <span className="font-normal text-ink-2">· {traversal.engine_ms.toFixed(1)} ms</span>
            <FocusTag className="ml-2">Multi-hop reasoning</FocusTag>
          </div>
          <div className="mt-2 flex flex-wrap items-baseline gap-x-2 gap-y-1 font-mono text-[12px] leading-[19px]">
            {traversal.nodes.map((n: string, i: number) => (
              <span key={i} className="flex items-baseline gap-2">
                {i > 0 && (
                  <span className="text-[10.5px] text-ink-2">
                    {(traversal.forward?.[i - 1] ?? i === 1) ? `${traversal.rels[i - 1]}→` : `←${traversal.rels[i - 1]}`}
                  </span>
                )}
                <span className={i === 1 ? "text-crimson" : i === traversal.nodes.length - 1 ? "text-ink" : "text-ink-1"}>
                  {String(n).replace(" MoE pretrain", "")}
                </span>
              </span>
            ))}
          </div>
          {impact?.gpus > 0 && (
            <div className="mt-3 flex gap-5 font-sans text-[12.5px] text-ink-1">
              {([[impact.gpus, "GPUs"], [impact.servers, "servers"], [impact.racks, "racks"], [impact.halls, "halls"]] as [number, string][]).map(([n, l]) => (
                <span key={l}><span className="text-crimson">{n.toLocaleString()}</span> {l}</span>
              ))}
              <span>{impact.campuses.join(" + ")}</span>
            </div>
          )}
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full overflow-visible font-mono">

        {/* edges: job - rings - tunnel */}
        {rings.map((r, i) => (
          <g key={i}>
            <Edge a={JOB} b={r} color={upColor} mode={hurt ? "trace" : applied ? "flow" : "solid"} />
            <Edge a={r} b={TUN} color={upColor} mode={hurt ? "trace" : applied ? "flow" : "solid"} />
          </g>
        ))}

        {/* tunnel to the cut cable: live, then broken, then abandoned */}
        <Edge
          a={TUN} b={CUT}
          color={applied ? DIM : inc ? CRIMSON : CYAN}
          mode={!inc ? "solid" : hurt ? "trace" : "dashed"}
        />
        {/* sibling cable sharing the trench */}
        {inc && sibling && traced && <Edge a={TUN} b={SIB} color={CRIMSON} mode="dashed" />}
        {/* the new carrier */}
        {routed && crossing && (
          <Edge a={TUN} b={NEW} color={AMBER} mode={applied ? "flow" : "dashed"} width={applied ? 1.6 : 1} />
        )}

        {/* job */}
        <circle cx={JOB.x} cy={JOB.y} r={26} fill="#02060F" stroke={nodeStroke} strokeWidth={1.2} className="transition-[stroke] duration-700" />
        <text x={JOB.x} y={JOB.y + 3} textAnchor="middle" fontSize={11} fill="#E6E8EB">Job</text>
        <text x={JOB.x} y={JOB.y + 44} textAnchor="middle" fontSize={12} fill="#E6E8EB">
          {(job?.model_name ?? "Llama-4 400B").replace(" MoE pretrain", "")}
        </text>


        {/* rings */}
        {rings.map((r, i) => (
          <circle key={i} cx={r.x} cy={r.y} r={5} fill="#02060F" stroke={nodeStroke} strokeWidth={1} className="transition-[stroke] duration-700" />
        ))}

        {/* tunnel */}
        <rect x={TUN.x - 14} y={TUN.y - 14} width={28} height={28} rx={3} fill="#02060F" stroke={nodeStroke} strokeWidth={1.2} transform={`rotate(45 ${TUN.x} ${TUN.y})`} className="transition-[stroke] duration-700" />
        <text x={TUN.x} y={TUN.y + 38} textAnchor="middle" fontSize={11} fill="#F2F4F7">
          Tunnel
        </text>

        {/* cut cable */}
        <circle cx={CUT.x} cy={CUT.y} r={9} fill="#02060F" stroke={inc ? CRIMSON : CYAN} strokeWidth={1.2} />
        {inc && (
          <g stroke={CRIMSON} strokeWidth={1.2}>
            <line x1={CUT.x - 5} y1={CUT.y - 5} x2={CUT.x + 5} y2={CUT.y + 5} />
            <line x1={CUT.x + 5} y1={CUT.y - 5} x2={CUT.x - 5} y2={CUT.y + 5} />
          </g>
        )}
        <text x={CUT.x + 18} y={CUT.y + 3} fontSize={13} fill={inc ? CRIMSON : "#E6E8EB"}>{cutName}</text>

        {/* sibling */}
        {inc && sibling && (
          <g opacity={traced ? 1 : 0} className="transition-opacity duration-700">
            <circle cx={SIB.x} cy={SIB.y} r={7} fill="#02060F" stroke={CRIMSON} strokeWidth={1} strokeDasharray="2 2" />
            <text x={SIB.x + 18} y={SIB.y + 3} fontSize={13} fill="#E6E8EB">{sibling}</text>
          </g>
        )}

        {/* new carrier */}
        {routed && crossing && (
          <g className="lens-appear">
            <circle cx={NEW.x} cy={NEW.y} r={9} fill="#02060F" stroke={AMBER} strokeWidth={1.4} />
            <circle cx={NEW.x} cy={NEW.y} r={3} fill={AMBER} />
            <text x={NEW.x + 18} y={NEW.y + 3} fontSize={13} fill={AMBER}>{s.cableName(crossing.cable)}</text>

          </g>
        )}

      </svg>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 border-t border-white/[0.06] pt-3 font-sans text-[12px] text-ink-2">
        {([[CYAN, "Live cable or link"], [CRIMSON, "Cut, or in the cut's trench"], [AMBER, "Detour the agent chose"]] as [string, string][]).map(([c, l]) => (
          <span key={l} className="flex items-center gap-2">
            <span className="inline-block h-[2px] w-4 rounded" style={{ background: c }} />
            {l}
          </span>
        ))}
      </div>
    </div>
  );
}
