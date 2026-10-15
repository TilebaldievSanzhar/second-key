// Общее для графиков: режим «на весь экран» (Esc — выйти, страница под ним не прокручивается)
// и высота от ширины, чтобы на широком экране график не превращался в полоску.
import { useEffect, useState } from "react";

export function useFullscreen() {
  const [full, setFull] = useState(false);
  const [viewH, setViewH] = useState(() => window.innerHeight);
  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setFull(false);
    const onResize = () => setViewH(window.innerHeight);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
    };
  }, [full]);
  const toggle = () => {
    setViewH(window.innerHeight);
    setFull((f) => !f);
  };
  return { full, toggle, viewH };
}

/** Высота графика: пропорционально ширине в разумных пределах. */
export const chartHeight = (width: number, ratio: number, min: number, max: number) =>
  Math.round(Math.min(max, Math.max(min, width * ratio)));
