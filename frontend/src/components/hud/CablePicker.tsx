"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { CableMeta } from "@/lib/types";
import { useMeshStore } from "@/store/useMeshStore";

const CARRIES: Record<string, string> = {
  GRADIENT_SYNC: "live training",
  CHECKPOINT_BURST: "checkpoints",
  BULK_BACKUP: "backups",
};

export function carries(c: CableMeta) {
  return c.traffic.length ? c.traffic.map((t) => CARRIES[t] ?? t).join(", ") : null;
}

function facts(c: CableMeta) {
  return [c.rfs ? `${c.rfs}` : null, c.length_km ? `${Math.round(c.length_km).toLocaleString()} km` : null, `${c.capacity_tbps} Tbps`]
    .filter(Boolean)
    .join("  /  ");
}

/** Loads the full cable catalog once per session. */
export function useCatalog() {
  const catalog = useMeshStore((s) => s.catalog);
  const set = useMeshStore((s) => s.set);
  useEffect(() => {
    if (!catalog.length) api.catalog().then((c) => set({ catalog: c })).catch(() => undefined);
  }, [catalog.length, set]);
  return catalog;
}

/** Searchable dropdown of every subsea cable, with metadata. */
export default function CablePicker({
  value,
  onChange,
  exclude,
  label = "Which cable breaks?",
}: {
  value: string | null;
  onChange: (id: string) => void;
  exclude?: string | null;
  label?: string;
}) {
  const catalog = useCatalog();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const selected = catalog.find((c) => c.id === value) ?? null;

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return catalog.filter(
      (c) =>
        c.id !== exclude &&
        (!needle ||
          c.name.toLowerCase().includes(needle) ||
          c.owners.toLowerCase().includes(needle) ||
          c.countries.some((x) => x.toLowerCase().includes(needle)) ||
          c.stations.some((x) => x.toLowerCase().includes(needle))),
    );
  }, [catalog, q, exclude]);

  return (
    <div ref={box} className="relative w-[460px]">
      <div className="font-sans text-[15px] text-silica">{label}</div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`mt-3 flex w-full items-center justify-between rounded-xl border bg-[#0d1626] px-4 py-3 text-left shadow-[0_10px_30px_rgba(0,0,0,0.45)] transition-colors ${
          open ? "border-white/30" : "border-white/[0.14] hover:border-white/30"
        }`}
      >
        <span className="min-w-0">
          <span className="block truncate font-sans text-[16px] tracking-tight text-silica">{selected?.name ?? "Choose a cable"}</span>
          {selected && <span className="block truncate font-sans text-[12px] text-silica/75">{facts(selected)}</span>}
        </span>
        <span className="ml-4 shrink-0 font-sans text-[12px] text-silica/75">{catalog.length} cables</span>
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-2 w-[520px] overflow-hidden rounded-xl border border-white/[0.1] bg-[#0d1626] shadow-[0_30px_80px_rgba(0,0,0,0.85)]">
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search cable, owner, country or landing station"
            className="w-full border-b border-white/[0.1] bg-[#111c30] px-4 py-3 font-sans text-[14px] text-silica placeholder:text-silica/55 focus:outline-none"
          />
          <div className="max-h-[300px] overflow-y-auto py-1">
            {list.slice(0, 120).map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => {
                  onChange(c.id);
                  setOpen(false);
                  setQ("");
                }}
                className={`block w-full px-4 py-2 text-left transition-colors hover:bg-[#16233a] ${c.id === value ? "bg-[#132036]" : ""}`}
              >
                <span className="flex items-baseline justify-between gap-3">
                  <span className="truncate font-sans text-[14px] tracking-tight text-silica">{c.name}</span>
                  <span className="shrink-0 font-sans text-[12px] tabular-nums text-silica/75">{facts(c)}</span>
                </span>
                <span className="flex items-baseline justify-between gap-3">
                  <span className="truncate font-sans text-[12px] text-silica/70">{c.countries.join(", ")}</span>
                  {carries(c) ? (
                    <span className="shrink-0 font-sans text-[12px] text-amber-laser/80">{carries(c)}</span>
                  ) : c.isolates.length ? (
                    <span className="shrink-0 font-sans text-[12px] text-crimson/80">isolates {c.isolates.length} countries</span>
                  ) : null}
                </span>
              </button>
            ))}
            {!list.length && <div className="px-4 py-3 font-sans text-[13px] text-silica/60">No cable matches.</div>}
            {list.length > 120 && (
              <div className="px-4 py-2 font-sans text-[12px] text-silica/55">{list.length - 120} more, refine the search</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Compact metadata card for the chosen cable. */
export function CableCard({ id }: { id: string | null }) {
  const catalog = useCatalog();
  const c = catalog.find((x) => x.id === id);
  if (!c) return null;
  // Two lines at most: what it carries, and what its failure does. The rest lives in the dropdown.
  const rows: [string, string][] = [["Carries", carries(c) ?? "none of our workloads"]];
  if (c.isolates.length) rows.push(["If it fails", `isolates ${c.isolates.slice(0, 3).join(", ")}${c.isolates.length > 3 ? ` +${c.isolates.length - 3}` : ""}`]);
  else if (c.atlantic_loss_tbps) rows.push(["If it fails", `-${Math.round(c.atlantic_loss_tbps)} Tbps across the Atlantic`]);
  return (
    <div className="mt-4 w-[460px] space-y-1.5 font-sans text-[13px]">
      {rows.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[96px_1fr] gap-3">
          <span className="text-silica/70">{k}</span>
          <span className="truncate text-silica" title={v}>
            {v}
          </span>
        </div>
      ))}
    </div>
  );
}
