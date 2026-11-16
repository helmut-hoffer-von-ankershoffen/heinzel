import { createContext, useContext } from "react";

/** Portaled tools opened inside a native modal must stay in its interactive subtree. */
export const OverlayTarget = createContext<() => HTMLElement>(() => document.body);

export function useOverlayTarget(): () => HTMLElement {
  return useContext(OverlayTarget);
}
