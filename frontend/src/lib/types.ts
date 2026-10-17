export type Station = {
  id: string;
  code: string;
  name: string;
  lat: number;
  lon: number;
  country_iso: string;
  kind: "LANDING_STATION" | "DC_GATEWAY";
};

export type Cable = {
  id: string;
  name: string;
  rfs: number | null;
  lines: [number, number][][];
};

export type SpanGeometry = Record<string, [number, number][]>;

export type CypherTrace = {
  label: string;
  cypher: string;
  params: Record<string, unknown>;
  engine_ms: number;
  wall_ms: number;
  rows: number;
  stats: Record<string, number>;
};

export type Hop = {
  from: string;
  to: string;
  cable: string;
  span_id: string;
  medium: "SUBSEA" | "BACKHAUL";
  latency_ms: number;
  srlg_id: string;
  available_capacity_tbps: number;
};

export type Candidate = {
  hop_sequence: string[];
  cables_used: string[];
  total_latency_ms: number | null;
  within_ceiling: boolean;
  srlg_isolation_verified: boolean;
  capacity_verified: boolean;
  hops: Hop[];
};

export type ToolResult = Record<string, any> & {
  cypher_trace?: CypherTrace[];
  engine_ms?: number;
  wall_ms?: number;
};

export type AgentStep = {
  step: number;
  tool: string;
  arguments?: Record<string, unknown>;
  result?: ToolResult;
  tool_wall_ms?: number;
};

export type AgentResult = {
  restored: boolean;
  mode: "playbook" | "llm";
  reason?: string | null;
  summary?: string;
  detour: Candidate | null;
  traffic_class?: string;
  rerouted_tunnels?: string[];
  restored_rings?: string[];
  restored_gpus?: number;
  spectrum_tbps?: number;
  ceiling_ms?: number;
  baseline_latency_ms?: number;
  detour_latency_ms?: number | null;
  latency_delta_ms?: number;
  jitter_budget_ms?: number;
  advisories?: string[];
  graph_engine_ms: number;
  graph_wall_ms: number;
  agent_wall_ms: number;
};

export type Incident = {
  incident_id: string;
  kind: string;
  cable_id: string;
  cable_name: string;
  severed_spans: { span_id: string; geo_id: string; from: string; to: string; srlg_id: string }[];
  fault_coordinate: { lon: number; lat: number } | null;
  started_at: number;
  agent_started_at: number | null;
  resolved_at: number | null;
  stall_active: boolean;
  degraded: boolean;
  blast_radius: ToolResult;
  sever_trace: CypherTrace;
  agent: (AgentResult & { steps?: { tool: string; result: ToolResult }[] }) | null;
};

export type Snapshot = {
  server_time: number;
  incident: Incident | null;
  stall_active: boolean;
  stall_seconds: number;
  loss_rate_usd_s: number;
  loss_usd: number;
  loss_rate_usd_hr: number;
  legacy_reconvergence_s: number;
};

export type FaultCable = { id: string; name: string; tunnels: string[]; traffic_classes: string[] };

export type Tunnel = {
  id: string;
  traffic_class: string;
  state: string;
  required_bandwidth_gbps: number;
  baseline_latency_ms: number;
  current_latency_ms: number;
  route_codes: string[];
  route_span_ids: string[];
  carried_by: string[];
};

export type Health = {
  falkordb: "up" | "down";
  graph: string;
  stations?: number;
  active_spans?: number;
  jobs?: number;
  llm_provider?: string;
  llm_model?: string;
};

type Pct = { n: number; p50: number; p95: number; p99: number; mean: number; min: number; max: number };

export type BenchmarkReport = {
  generated_at: string;
  duration_s: number;
  mode: string;
  iterations: Record<string, number>;
  machine: Record<string, string>;
  workload: { question: string; stations: number; spans_directed: number };
  parity: { topology_file: string; verified: boolean };
  results_agree: { falkordb: number | null; networkx: number | null; falkordb_equals_networkx: boolean };
  falkordb: {
    detour_search: { wall_ms: Pct; engine_ms: Pct };
    detour_tool_cycle: { wall_ms: Pct };
    centrality_sampled: { wall_ms: Pct; engine_ms: Pct };
    centrality_exact: { wall_ms: Pct; engine_ms: Pct };
  };
  networkx: {
    detour_search: { wall_ms: Pct };
    centrality_sampled: { wall_ms: Pct };
    centrality_exact: { wall_ms: Pct };
  };
  sqlite: {
    runs: {
      max_hops: number;
      status: "OK" | "TIMEOUT";
      timeout_s: number;
      wall_ms: Pct;
      best_path: { latency_ms: number; hops: number } | null;
    }[];
  };
};

export type CableMeta = {
  id: string;
  name: string;
  length_km: number | null;
  capacity_tbps: number;
  capacity_model: string;
  rfs: number | null;
  owners: string;
  risk: number | null;
  risk_rank: number | null;
  isolates: string[];
  atlantic_loss_tbps: number | null;
  gpus_at_risk: number | null;
  stations: string[];
  countries: string[];
  traffic: string[];
};

export type ProofEngine = "falkordb" | "sqlite" | "networkx";
export type ProofImpact = { gpus: number; servers: number; racks: number; halls: number; campuses: string[]; rings: number; hops: number };
export type LiveProof = {
  cable_id: string;
  cable_name: string;
  generated_at: string;
  duration_s: number;
  runs: Record<ProofEngine, number>;
  snapshot: { copy_ms: number; severed_spans: number; export_ms: number; edges: number };
  parity: Record<ProofEngine, string> & { verified: boolean };
  questions: {
    hurt: { label: string; answer: Record<ProofEngine, ProofImpact>; agree: { sqlite: boolean | null; networkx: boolean | null } };
    detour: {
      label: string;
      inputs: { src: string; dst: string; srlg: string; req: number; ceiling: number; source: string };
      answer: { falkordb: number | null; sqlite: number | null; networkx: number | null; hops: number };
      agree: { sqlite: boolean | null; networkx: boolean | null };
    };
  };
  engines: {
    falkordb: { hurt_ms: number; detour_ms: number; total_ms: number; hurt_engine_ms: number; detour_engine_ms: number };
    networkx: { hurt_ms: number; detour_ms: number; total_ms: number };
    sqlite: {
      hurt_ms: number; detour_ms: number; total_ms: number; timed_out: boolean; hurt_timed_out: boolean;
      detour_timed_out: boolean; detour_hinted_ms: number | null; detour_hinted_hops: number | null;
    };
  };
  speedup: { vs_sqlite: number; vs_sqlite_is_lower_bound: boolean; sql_timeout_s: number; vs_networkx: number };
};
export type ProofState = { running: boolean; stage: string | null; cableId: string | null; report: LiveProof | null; error: string | null };
