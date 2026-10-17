import { useEffect, useState } from "react";
import { useMeshStore } from "./useMeshStore";
import { useAgentView } from "./useAgentView";
import type { AgentResult, AgentStep, Incident, Station, ToolResult } from "@/lib/types";

export type Phase = "idle" | "cut" | "thinking" | "fixed" | "failed";

export const TOOL_ORDER = [
  "trace_severed_blast_radius",
  "compute_optimal_optical_detour",
  "audit_secondary_choke_points",
  "execute_spectrum_reallocation",
] as const;

/** Human place name for a station code: "Wall Township, NJ, United States" -> "Wall Township". */
export function placeName(code: string, byCode: Map<string, Station>) {
  const st = byCode.get(code);
  if (!st) return code;
  if (st.kind === "DC_GATEWAY") return code === "IAD" ? "Ashburn" : code === "DUB" ? "Dublin" : st.name;
  return st.name.split(",")[0];
}

export function countryName(code: string, byCode: Map<string, Station>) {
  const st = byCode.get(code);
  if (!st) return "";
  const parts = st.name.split(",").map((s) => s.trim());
  return parts[parts.length - 1];
}

export type Story = {
  phase: Phase;
  inc: Incident | null;
  steps: AgentStep[];
  result: AgentResult | null;
  byStep: (tool: string) => ToolResult | undefined;
  stalls: boolean;
  gpus: number;
  jobName: string;
  ratePerSec: number;
  stallSeconds: number;
  lossUsd: number;
  agentSeconds: number;
  legacySeconds: number;
  byCode: Map<string, Station>;
  newCable: string | null;
  cutCable: string | null;
  cableName: (id: string | null | undefined) => string;
};

/** One place that turns raw backend state into the plain-English story the screen tells. */
export function useStory(): Story {
  const snapshot = useMeshStore((s) => s.snapshot);
  const stations = useMeshStore((s) => s.stations);
  const cables = useMeshStore((s) => s.cables);
  const agentRunning = useMeshStore((s) => s.agentRunning);
  const queued = useMeshStore((s) => s.queue.length);
  const clockOffset = useMeshStore((s) => s.clockOffset);
  const { steps, result } = useAgentView();

  const inc = snapshot?.incident ?? null;
  const [, tick] = useState(0);
  const live = !!inc && !inc.resolved_at;
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => tick((t) => t + 1), 100);
    return () => clearInterval(id);
  }, [live]);

  const byCode = new Map(stations.map((s) => [s.code, s]));
  const blast = inc?.blast_radius;
  const stalls = !!blast?.affected_tunnels?.some((t: any) => t.traffic_class === "GRADIENT_SYNC");
  const jobs = blast?.affected_jobs ?? [];
  // Frozen = jobs whose gradient sync rides the cut; checkpoint or backup traffic slows a job, it does not stop it.
  const syncJobs = new Set(
    (blast?.affected_tunnels ?? []).filter((t: any) => t.traffic_class === "GRADIENT_SYNC").flatMap((t: any) => t.jobs),
  );
  const gpus = jobs.filter((j: any) => syncJobs.has(j.job_id)).reduce((a: number, j: any) => a + j.gpu_count, 0);
  const ratePerSec = (snapshot?.loss_rate_usd_hr ?? 18420) / 3600;
  const now = Date.now() / 1000 + clockOffset;
  const stallSeconds = inc && stalls ? Math.max(0, (inc.resolved_at ?? now) - inc.started_at) : 0;

  let phase: Phase = "idle";
  if (inc) phase = "cut";
  if (inc && (agentRunning || queued || (steps.length > 0 && !result))) phase = "thinking";
  if (inc && result && !agentRunning && !queued) phase = result.restored ? "fixed" : "failed";

  const byStep = (tool: string) => steps.find((s) => s.tool === tool)?.result;
  const subsea = result?.detour?.hops.filter((h) => h.medium === "SUBSEA") ?? [];

  return {
    phase,
    inc,
    steps,
    result,
    byStep,
    stalls,
    gpus,
    jobName: jobs[0]?.model_name ?? "",
    ratePerSec,
    stallSeconds,
    lossUsd: stallSeconds * ratePerSec,
    agentSeconds: result ? result.agent_wall_ms / 1000 : 0,
    legacySeconds: snapshot?.legacy_reconvergence_s ?? 180,
    byCode,
    newCable: subsea[0]?.cable ?? null,
    cutCable: inc?.cable_name ?? null,
    cableName: (id) => (id ? (cables.find((c) => c.id === id)?.name ?? id) : ""),
  };
}

