import { type RefObject, useEffect, useState } from "react";

/** The width of `ref`'s element as it changes; the window's until it is measured (and where nothing measures). */
export function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const element = ref.current;
    const measure = () => setWidth(element?.getBoundingClientRect().width || window.innerWidth);
    measure();
    window.addEventListener("resize", measure);
    const observer = typeof ResizeObserver === "undefined" || !element ? null : new ResizeObserver(measure);
    if (element) observer?.observe(element);
    return () => {
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, [ref]);
  return width;
}
