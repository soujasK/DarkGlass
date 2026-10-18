"use client";

import dynamic from "next/dynamic";
import { useEffect } from "react";
import { api, loadStatic, wsUrl } from "@/lib/api";
import type { Cable, SpanGeometry, Station } from "@/lib/types";
import { engineBase, useMeshStore, type WsEvent } from "@/store/useMeshStore";
import CuratorsDrawer from "@/components/hud/CuratorsDrawer";
import Chapter from "@/components/hud/Chapter";
import { TopBar } from "@/components/hud/Chrome";
import EngineBar from "@/components/hud/EngineBar";
import { AskPanel, ProofPanel, ScanPanel } from "@/components/hud/Modes";
import GraphLens from "@/components/hud/GraphLens";

const GlobeScene = dynamic(() => import("@/components/canvas/GlobeScene"), { ssr: false });

/** Agent events arrive within ~100 ms; they are replayed at this cadence so a viewer can follow them. */
const REPLAY_MS = 700;

function useBootstrap() {
  const set = useMeshStore((s) => s.set);
  const setSnapshot = useMeshStore((s) => s.setSnapshot);

  useEffect(() => {
    // Deep link to a screen: /#scan, /#ask, /#proof
    const linked = window.location.hash.slice(1);
    if (linked === "scan" || linked === "ask" || linked === "proof") set({ mode: linked });
  }, [set]);

  useEffect(() => {
    Promise.all([
      loadStatic<Station[]>("stations.json"),
      loadStatic<Cable[]>("cables.json"),
      loadStatic<SpanGeometry>("span_geometry.json"),
    ])
      .then(([stations, cables, spanGeo]) => set({ stations, cables, spanGeo, dataReady: true }))
      .catch((e) => set({ dataError: (e as Error).message }));

    const refresh = async () => {
      try {
        const [health, snap, tunnels, bench, faultCables, proof] = await Promise.all([
          api.health(),
          api.state(),
          api.tunnels(),
          api.benchmark(),
          api.cables(),
          api.proof(),
        ]);
        set({
          health, tunnels, faultCables, benchmark: bench.report, benchmarkRunning: bench.running,
          proof: { running: proof.running, stage: proof.stage, cableId: proof.cable_id, report: proof.report, error: proof.error },
        });
        const st = useMeshStore.getState();
        if (health.llm_available === false && st.agentMode === "llm") set({ agentMode: "playbook" });
        if (!st.engine) {
          const engine = await api.engine();
          set({ engine, engineReceivedAt: performance.now() });
        }
        if (!st.scan.summary && !st.scan.running) {
          const latest = await api.latestScan();
          if (latest.summary) set({ scan: { ...useMeshStore.getState().scan, summary: latest.summary, heat: latest.summary.heat } });
        }
        setSnapshot(snap);
      } catch {
        set({ health: { falkordb: "down", graph: "unreachable" } });
      }
    };
    refresh();
    const id = setInterval(refresh, 10_000);
    return () => clearInterval(id);
  }, [set, setSnapshot]);
}

function useEventStream() {
  const set = useMeshStore((s) => s.set);
  const setSnapshot = useMeshStore((s) => s.setSnapshot);
  const enqueue = useMeshStore((s) => s.enqueue);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      set({ wsStatus: "connecting" });
      ws = new WebSocket(wsUrl());
      ws.onopen = async () => {
        set({ wsStatus: "open" });
        // Re-sync after any reconnect so events missed while disconnected cannot leave stale state.
        try {
          const [snap, tunnels, health] = await Promise.all([api.state(), api.tunnels(), api.health()]);
          setSnapshot(snap);
          set({ tunnels, health });
        } catch {
          /* backend still starting; the periodic refresh will catch up */
        }
      };
      ws.onclose = () => {
        set({ wsStatus: "closed" });
        if (!closed) retry = setTimeout(connect, 2000);
      };
      ws.onmessage = async (msg) => {
        const e: WsEvent = JSON.parse(msg.data);
        if (e.type.startsWith("agent.")) {
          enqueue(e);
        } else if (e.type === "chaos.severed" || e.type === "reset") {
          setSnapshot(e.payload);
          set({ notice: null }); // an old error does not belong to the new scenario
          set({ tunnels: await api.tunnels(), health: await api.health() });
        } else if (e.type === "state") {
          // Resolution is applied when the replay reaches agent.complete (see useReplay).
          enqueue(e);
        } else if (e.type === "engine.stats") {
          const st = useMeshStore.getState();
          set({ engine: e.payload, engineReceivedAt: performance.now(),
                ...(st.engineBase ? {} : { engineBase: engineBase(e.payload, "idle") }) });
        } else if (e.type.startsWith("scan.")) {
          const cur = useMeshStore.getState().scan;
          const p = e.payload;
          if (e.type === "scan.started") {
            set({ scan: { ...cur, running: true, done: 0, total: p.total, elapsedMs: 0, heat: {}, summary: null, workers: p.workers } });
          } else if (e.type === "scan.progress") {
            const heat = { ...cur.heat };
            for (const b of p.batch as { id: string; risk: number }[]) heat[b.id] = Math.min(1, Math.sqrt(b.risk / 90));
            set({ scan: { ...cur, running: true, done: p.done, total: p.total, elapsedMs: p.elapsed_ms, heat } });
          } else if (e.type === "scan.complete") {
            set({ scan: { ...cur, running: false, done: p.summary.scenarios, summary: p.summary, heat: p.summary.heat } });
          } else if (e.type === "scan.error") {
            set({ scan: { ...cur, running: false }, notice: p.error });
          }
        } else if (e.type.startsWith("proof.")) {
          const cur = useMeshStore.getState().proof;
          const p = e.payload;
          if (e.type === "proof.started") {
            set({ proof: { ...cur, running: true, stage: "snapshot", cableId: p.cable_id, error: null } });
            // Only a proof started from the Proof tab gets its own count; the automatic one after a fix
            // must not wipe the incident's numbers off the screen.
            const now = useMeshStore.getState();
            if (now.mode === "proof" && now.engine) set({ engineBase: engineBase(now.engine, "this proof") });
          }
          else if (e.type === "proof.progress") set({ proof: { ...cur, running: true, stage: p.stage, cableId: p.cable_id } });
          else if (e.type === "proof.complete") set({ proof: { running: false, stage: null, cableId: p.report.cable_id, report: p.report, error: null } });
          else if (e.type === "proof.error") set({ proof: { ...cur, running: false, stage: null, error: p.error } });
        } else if (e.type.startsWith("benchmark.")) {
          if (e.type === "benchmark.started") set({ benchmarkRunning: true });
          else {
            const b = await api.benchmark();
            set({ benchmark: b.report, benchmarkRunning: b.running });
          }
        }
      };
    };
    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      ws?.close();
    };
  }, [set, setSnapshot, enqueue]);
}

function useReplay() {
  const queueLength = useMeshStore((s) => s.queue.length);
  useEffect(() => {
    if (!queueLength) return;
    const id = setTimeout(async () => {
      const { queue, applyEvent, setSnapshot, set } = useMeshStore.getState();
      const [head, ...rest] = queue;
      set({ queue: rest });
      if (head.type === "state") {
        setSnapshot(head.payload);
        set({ tunnels: await api.tunnels() });
      } else {
        applyEvent(head);
      }
    }, REPLAY_MS);
    return () => clearTimeout(id);
  }, [queueLength]);
}

function ModeView() {
  const mode = useMeshStore((s) => s.mode);
  if (mode === "scan") return <ScanPanel key="scan" />;
  if (mode === "ask") return <AskPanel key="ask" />;
  if (mode === "proof") return <ProofPanel key="proof" />;
  return (
    <>
      <Chapter />
      <GraphLens />
    </>
  );
}

export default function Page() {
  useBootstrap();
  useEventStream();
  useReplay();
  const dataError = useMeshStore((s) => s.dataError);
  const dataReady = useMeshStore((s) => s.dataReady);

  return (
    <main className="relative h-screen w-screen overflow-hidden bg-void">
      <GlobeScene />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_55%,rgba(2,6,15,0.85)_100%)]" />
      <div className="pointer-events-none absolute inset-y-0 left-0 z-10 w-[62vw] bg-gradient-to-r from-void via-void/85 to-transparent" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-[380px] bg-gradient-to-t from-void via-void/85 to-transparent" />
      <TopBar />
      <ModeView />
      <EngineBar />
      <CuratorsDrawer />
      {!dataReady && (
        <div className="absolute inset-0 z-50 flex items-center justify-center font-mono text-hud text-muted">
          {dataError ?? "LOADING PHYSICAL PLANT"}
        </div>
      )}
    </main>
  );
}
