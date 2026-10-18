import { create } from "zustand";
import type {
  AgentResult,
  AgentStep,
  BenchmarkReport,
  Cable,
  CableMeta,
  Candidate,
  FaultCable,
  Health,
  Snapshot,
  SpanGeometry,
  Station,
  ProofState,
  Tunnel,
} from "@/lib/types";

export type WsEvent = { type: string; ts: number; payload: any };

export type Mode = "incident" | "scan" | "ask" | "proof";

export type EngineCapability = {
  key: string;
  name: string;
  does: string;
  calls: number;
  engine_ms: number;
  last_ms: number;
  age_s: number | null;
};

/** Engine counters at the start of the current scope, so the bar shows this incident's work, not the session's. */
export type EngineBase = { total: number; by: Record<string, number>; scope: string };
export const engineBase = (e: EngineSnapshot, scope: string): EngineBase => ({
  total: e.total_calls,
  by: Object.fromEntries(e.capabilities.map((c) => [c.key, c.calls])),
  scope,
});

export type EngineSnapshot = {
  capabilities: EngineCapability[];
  recent: { kind: string; query: string; engine_ms: number; rows: number; at: number }[];
  queries_per_s: number;
  total_calls: number;
  total_engine_ms: number;
  server_time: number;
};

export type ScanCard = {
  id: string;
  name: string;
  rank: number;
  risk: number;
  isolated: number;
  countries: string[];
  gpus: number;
  stalls: boolean;
  latency_delta: number | null;
  unreachable: boolean;
  capacity_loss: number;
};

export type ScanSummary = {
  id: string;
  scenarios: number;
  workers: number;
  duration_ms: number;
  scan_ms: number;
  graph_engine_ms: number;
  baseline_transatlantic_tbps: number;
  cables_that_island_stations: number;
  cables_that_stall_training: number;
  top_cluster: ScanCard[];
  top_planet: ScanCard[];
  top_capacity: ScanCard[];
  heat: Record<string, number>;
};

export type AskResult = {
  question: string;
  cypher: string;
  columns?: string[];
  rows?: Record<string, unknown>[];
  row_count?: number;
  engine_ms?: number;
  llm_ms?: number;
  model?: string;
  error?: string;
  highlight?: { stations: string[]; cables: string[] };
};

type MeshState = {
  // static physical plant (frontend JSON bundles written by ingestion)
  stations: Station[];
  cables: Cable[];
  spanGeo: SpanGeometry;
  dataReady: boolean;
  dataError: string | null;

  // live backend state
  health: Health | null;
  tunnels: Tunnel[];
  faultCables: FaultCable[];
  catalog: CableMeta[];
  selectedCable: string;
  snapshot: Snapshot | null;
  clockOffset: number; // server_time - local time, seconds
  wsStatus: "connecting" | "open" | "closed";

  // agent run, replayed at human pace from the event stream
  queue: WsEvent[];
  steps: AgentStep[];
  agentRunning: boolean;
  agentMode: "playbook" | "llm";
  agentResult: AgentResult | null;
  detour: Candidate | null;
  detourIgnitedAt: number | null;
  thoughts: string[];

  // modes and FalkorDB engine
  mode: Mode;
  engine: EngineSnapshot | null;
  engineReceivedAt: number;
  engineBase: EngineBase | null;
  scan: { running: boolean; done: number; total: number; elapsedMs: number; heat: Record<string, number>; summary: ScanSummary | null };
  askState: { loading: boolean; result: AskResult | null; error: string | null };

  // presentation
  drawerOpen: boolean;
  selectedTrace: { step: number; index: number } | null;
  benchmark: BenchmarkReport | null;
  benchmarkRunning: boolean;
  proof: ProofState;
  notice: string | null;

  set: (partial: Partial<MeshState>) => void;
  setSnapshot: (s: Snapshot) => void;
  enqueue: (e: WsEvent) => void;
  applyEvent: (e: WsEvent) => void;
  clearRun: () => void;
};

export const useMeshStore = create<MeshState>((set, get) => ({
  stations: [],
  cables: [],
  spanGeo: {},
  dataReady: false,
  dataError: null,

  health: null,
  tunnels: [],
  faultCables: [],
  catalog: [],
  selectedCable: "dunant",
  snapshot: null,
  clockOffset: 0,
  wsStatus: "connecting",

  queue: [],
  steps: [],
  agentRunning: false,
  agentMode: "llm",
  agentResult: null,
  detour: null,
  detourIgnitedAt: null,
  thoughts: [],

  mode: "incident",
  engine: null,
  engineReceivedAt: 0,
  engineBase: null,
  scan: { running: false, done: 0, total: 0, elapsedMs: 0, heat: {}, summary: null },
  askState: { loading: false, result: null, error: null },

  drawerOpen: false,
  selectedTrace: null,
  benchmark: null,
  benchmarkRunning: false,
  proof: { running: false, stage: null, cableId: null, report: null, error: null },
  notice: null,

  set: (partial) => set(partial),

  setSnapshot: (s) => {
    const st = get();
    const extra: Partial<MeshState> = {};
    const persisted = s.incident?.agent;
    const changed = (st.snapshot?.incident?.incident_id ?? null) !== (s.incident?.incident_id ?? null);
    if (changed && !st.agentRunning) {
      // A reset or a new cut from anywhere (another tab, the API): the previous run's result no longer applies.
      Object.assign(extra, { steps: [], agentResult: null, thoughts: [], selectedTrace: null });
    }
    if (changed && st.engine) extra.engineBase = engineBase(st.engine, s.incident ? "this incident" : "since the last reset");
    if (!s.incident) {
      extra.detour = null;
      extra.detourIgnitedAt = null;
    } else if (persisted && (changed || !st.agentResult) && !st.agentRunning && !st.queue.length) {
      // Page loaded after the agent already ran: show its result and corridor without replaying.
      extra.agentResult = persisted;
      extra.steps = (persisted.steps ?? []).map((x, i) => ({ step: i + 1, tool: x.tool, result: x.result }));
      if (persisted.restored && persisted.detour) {
        extra.detour = persisted.detour;
        extra.detourIgnitedAt = performance.now();
      }
    } else if (!persisted && s.incident.agent_started_at && !st.agentRunning) {
      // Page loaded while the agent is running: its remaining events fill the screen in.
      extra.agentRunning = true;
    }
    set({ snapshot: s, clockOffset: s.server_time - Date.now() / 1000, ...extra });
  },

  enqueue: (e) => set({ queue: [...get().queue, e] }),

  clearRun: () =>
    set({
      queue: [],
      steps: [],
      agentRunning: false,
      agentResult: null,
      detour: null,
      detourIgnitedAt: null,
      thoughts: [],
      selectedTrace: null,
    }),

  applyEvent: (e) => {
    const p = e.payload;
    switch (e.type) {
      case "agent.start":
        set({ agentRunning: true, steps: [], thoughts: [], agentResult: null, detour: null });
        break;
      case "agent.tool_call":
        set({ steps: [...get().steps, { step: p.step, tool: p.tool, arguments: p.arguments }] });
        break;
      case "agent.tool_result": {
        const steps = get().steps.map((s) =>
          s.step === p.step ? { ...s, result: p.result, tool_wall_ms: p.tool_wall_ms } : s,
        );
        set({ steps, selectedTrace: { step: p.step, index: 0 } });
        break;
      }
      case "agent.thought":
        set({ thoughts: [...get().thoughts, p.text] });
        break;
      case "agent.complete":
        set({
          agentRunning: false,
          agentResult: p,
          detour: p.restored ? p.detour : null,
          detourIgnitedAt: p.restored ? performance.now() : null,
        });
        break;
      case "agent.error":
        // Drop the partial run so the screen goes back to the fix button and the agent picker.
        set({ agentRunning: false, steps: [], thoughts: [], notice: `agent error: ${p.error}. Pick Playbook to run without Gemini.` });
        break;
      default:
        break;
    }
  },
}));
