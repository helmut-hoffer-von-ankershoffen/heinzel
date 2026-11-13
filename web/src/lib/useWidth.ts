import { type RefObject, useEffect, useState } from "react";

/** Tailwind's `sm` breakpoint, 40rem at the 16px rem media queries use: a window narrower than this is a phone's. */
export const PHONE_BELOW_PX = 640;

/** Whether a window `width` px wide gets the phone layout, the same switch as Tailwind's `max-sm:` variant. */
export function isPhoneWidth(width: number): boolean {
  return width < PHONE_BELOW_PX;
}

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
