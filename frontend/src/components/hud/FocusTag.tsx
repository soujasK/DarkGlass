import type { ReactNode } from "react";

/** Names the Track 01 key focus an element demonstrates, in the hackathon's own words. */
export default function FocusTag({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span
      title="Track 01 key focus"
      className={`inline-flex items-center whitespace-nowrap rounded-md border border-phosphor/40 px-1.5 align-[2px] font-sans text-[11px] font-normal leading-[17px] text-phosphor ${className}`}
    >
      {children}
    </span>
  );
}
