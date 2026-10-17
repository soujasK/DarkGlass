"use client";

const DIGITS = "0123456789";

/** Mechanical drum counter: one rolling column per digit, fixed glyphs for everything else. */
export default function Roll({ text, className }: { text: string; className?: string }) {
  return (
    <span className={`inline-flex tabular-nums ${className ?? ""}`}>
      {text.split("").map((ch, i) =>
        DIGITS.includes(ch) ? (
          <span key={i} className="relative inline-block h-[1em] w-[0.62em] overflow-hidden leading-none">
            <span
              className="absolute left-0 top-0 flex flex-col transition-transform duration-200 ease-out"
              style={{ transform: `translateY(-${Number(ch)}em)` }}
            >
              {DIGITS.split("").map((d) => (
                <span key={d} className="block h-[1em] leading-none">
                  {d}
                </span>
              ))}
            </span>
          </span>
        ) : (
          <span key={i} className="inline-block leading-none">
            {ch}
          </span>
        ),
      )}
    </span>
  );
}
