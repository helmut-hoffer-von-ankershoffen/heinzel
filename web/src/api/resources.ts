import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, problem, unwrap } from "./client";
import { keys } from "./queries";
import { token } from "./token";

/** Resource sampling is server-owned; null means that reading is unavailable. */
export type Resources = {
  sampled_at: string;
  cpu_percent: number | null;
  memory_percent: number | null;
  load_1m: number | null;
  logical_cores: number | null;
  problems: string[];
};

function useVisible() {
  const [visible, setVisible] = useState(() => document.visibilityState !== "hidden");
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}

// This operation's generated client lands with the server sampler. Keep its interim wire boundary checked.
async function resources(signal: AbortSignal): Promise<Resources> {
  const response = await fetch(
    new Request(new URL("/api/system/resource", window.location.origin), {
      signal,
      headers: { Authorization: `Bearer ${token() ?? ""}` },
    }),
  );
  const body: unknown = await response.json();
  if (!response.ok) throw problem(response.status, body);
  if (!body || typeof body !== "object") throw new Error("Invalid resource report");
  const report = body as Record<string, unknown>;
  const reading = (name: string, max = Number.POSITIVE_INFINITY) => {
    const value = report[name];
    return value === null || (typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= max);
  };
  if (
    typeof report.sampled_at !== "string" ||
    !Number.isFinite(Date.parse(report.sampled_at)) ||
    !reading("cpu_percent", 100) ||
    !reading("memory_percent", 100) ||
    !reading("load_1m") ||
    !(report.logical_cores === null || (Number.isInteger(report.logical_cores) && Number(report.logical_cores) > 0)) ||
    !Array.isArray(report.problems) ||
    !report.problems.every((entry) => typeof entry === "string")
  )
    throw new Error("Invalid resource report");
  return report as Resources;
}

export function useResources() {
  const visible = useVisible();
  return useQuery({
    queryKey: ["system", "resource"],
    queryFn: ({ signal }) => resources(signal),
    enabled: visible,
    staleTime: 0,
    refetchInterval: visible ? 3000 : false,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

export function useFooterHealth() {
  const visible = useVisible();
  return useQuery({
    queryKey: keys.doctor,
    queryFn: ({ signal }) => unwrap(api.GET("/system/doctor", { signal })),
    enabled: visible,
    staleTime: 0,
    refetchInterval: visible ? 30_000 : false,
    refetchOnWindowFocus: false,
    retry: false,
  });
}
