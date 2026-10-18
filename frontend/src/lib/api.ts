import type { BenchmarkReport, CableMeta, FaultCable, Health, LiveProof, Snapshot, Tunnel } from "./types";

// An empty NEXT_PUBLIC_API_URL means the API is served from the page's own address (behind a proxy).
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/api/health"),
  state: () => request<Snapshot>("/api/state"),
  tunnels: () => request<Tunnel[]>("/api/tunnels"),
  cables: () => request<FaultCable[]>("/api/cables"),
  catalog: () => request<CableMeta[]>("/api/cables/catalog"),
  anchorDrag: (cable_id = "dunant") =>
    request<Snapshot>("/api/chaos/anchor-drag", { method: "POST", body: JSON.stringify({ cable_id }) }),
  runAgent: (mode: "playbook" | "llm") =>
    request<(Snapshot & { notice?: string }) | { accepted: true }>("/api/agent/run", { method: "POST", body: JSON.stringify({ mode }) }),
  runScan: () => request<{ accepted: boolean }>("/api/scan/run", { method: "POST" }),
  latestScan: () => request<{ summary: import("@/store/useMeshStore").ScanSummary | null }>("/api/scan/latest"),
  ask: (question: string) =>
    request<import("@/store/useMeshStore").AskResult>("/api/ask", { method: "POST", body: JSON.stringify({ question }) }),
  tabQueries: (tab: "scan" | "proof") =>
    request<{ tab: string; queries: { name: string; does: string; language: string; text: string }[] }>(`/api/queries/${tab}`),
  engine: () => request<import("@/store/useMeshStore").EngineSnapshot>("/api/engine"),
  reset: () => request<Snapshot>("/api/reset", { method: "POST" }),
  benchmark: () =>
    request<{ running: boolean; error: string | null; report: BenchmarkReport | null; reverts_at?: number }>("/api/benchmark"),
  proof: () =>
    request<{ running: boolean; cable_id: string | null; stage: string | null; error: string | null; report: LiveProof | null }>(
      "/api/proof/live",
    ),
  runProof: (cable_id?: string) =>
    request<{ accepted: boolean; cable_id: string }>("/api/proof/live", { method: "POST", body: JSON.stringify({ cable_id }) }),
  runBenchmark: (quick = true) =>
    request<{ accepted: boolean }>("/api/benchmark/run", { method: "POST", body: JSON.stringify({ quick }) }),
};

export function wsUrl() {
  const base = API_URL || window.location.origin;
  return base.replace(/^http/, "ws") + "/ws";
}

export async function loadStatic<T>(name: string): Promise<T> {
  const res = await fetch(`/data/${name}`);
  if (!res.ok) throw new Error(`missing /data/${name} - run backend ingestion first`);
  return res.json() as Promise<T>;
}
