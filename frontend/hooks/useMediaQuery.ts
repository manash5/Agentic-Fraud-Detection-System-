"use client";

import { useSyncExternalStore } from "react";

/** SSR-safe media query hook, defaults to `false` until hydrated on the client. */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/** True at Tailwind's `lg` breakpoint (1024px) and above. */
export function useIsDesktop(): boolean {
  return useMediaQuery("(min-width: 1024px)");
}
