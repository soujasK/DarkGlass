"use client";

import { api } from "@/lib/api";
import { useMeshStore, type Mode } from "@/store/useMeshStore";

const MODES: { key: Mode; label: string }[] = [
  { key: "incident", label: "Incident" },
  { key: "scan", label: "Planet scan" },
  { key: "ask", label: "Ask the graph" },
  { key: "proof", label: "Proof" },
];

export function TopBar() {
  const st = useMeshStore();
  const up = st.health?.falkordb === "up";

  const reset = async () => {
    let snap;
    try {
      snap = await api.reset(); // the server refuses while the agent is still working
    } catch (e) {
      st.set({ notice: (e as Error).message });
      return;
    }
    st.clearRun();
    // Back to the start: the Incident tab's "Which cable breaks?" screen, whichever tab Reset was pressed on.
    st.set({ notice: null, mode: "incident", drawerOpen: false });
    st.setSnapshot(snap);
    const [tunnels, health, faultCables] = await Promise.all([api.tunnels(), api.health(), api.cables()]);
    st.set({ tunnels, health, faultCables, askState: { loading: false, result: null, error: null } });
  };

  return (
    <header className="absolute inset-x-0 top-0 z-30 flex items-center justify-between px-16 pt-8">
      <div className="flex items-baseline gap-4">
        <span className="font-sans text-[15px] font-semibold tracking-[-0.02em] text-ink">DarkGlass</span>
        <a
          href="https://www.submarinecablemap.com"
          target="_blank"
          rel="noreferrer"
          className="font-sans text-[12px] text-ink-3 transition-colors hover:text-ink-1"
          title="Cable and landing-station data: TeleGeography Submarine Cable Map, CC BY-NC-SA 3.0"
        >
          Cable data: TeleGeography
        </a>
      </div>

      <nav className="flex items-center gap-1 rounded-full border border-white/[0.07] bg-void/60 p-1 backdrop-blur-md">
        {MODES.map((m) => (
          <button
            key={m.key}
            type="button"
            onClick={() => st.set({ mode: m.key })}
            className={`rounded-full px-4 py-1.5 font-sans text-[13px] tracking-tight transition-colors ${
              st.mode === m.key ? "bg-silica text-void" : "text-ink-1 hover:text-ink"
            }`}
          >
            {m.label}
          </button>
        ))}
      </nav>

      <div className="flex items-center gap-6 font-sans text-[13px] text-ink-2">
        <span className="flex items-center gap-2">
          <span className={`h-1.5 w-1.5 rounded-full ${up ? "bg-[#34d399]" : "bg-crimson"}`} />
          {up ? "Live" : "Offline"}
        </span>
        <button type="button" onClick={reset} className="transition-colors hover:text-ink">
          Reset
        </button>
      </div>
    </header>
  );
}
