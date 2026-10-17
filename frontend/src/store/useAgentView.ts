import type { AgentResult, AgentStep } from "@/lib/types";
import { useMeshStore } from "./useMeshStore";

/**
 * Agent steps and result as the UI should show them: the live replay while it runs,
 * otherwise what the backend persisted on the incident (so a page reload keeps the story).
 */
export function useAgentView(): { steps: AgentStep[]; result: AgentResult | null } {
  const liveSteps = useMeshStore((s) => s.steps);
  const liveResult = useMeshStore((s) => s.agentResult);
  const running = useMeshStore((s) => s.agentRunning);
  const queued = useMeshStore((s) => s.queue.length);
  const persisted = useMeshStore((s) => s.snapshot?.incident?.agent ?? null);

  if (liveSteps.length || running || queued) return { steps: liveSteps, result: liveResult };
  if (!persisted) return { steps: [], result: null };
  return {
    steps: (persisted.steps ?? []).map((s, i) => ({ step: i + 1, tool: s.tool, result: s.result })),
    result: persisted,
  };
}
