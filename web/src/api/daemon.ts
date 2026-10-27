import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type SchemaDaemonStatus, type SchemaRestarted, type SchemaToggled, unwrap } from "./client";

export const daemonStatusKey = ["system", "daemon", "status"] as const;
export type DaemonAction = "pause" | "resume" | "restart";

/** The existing daemon's facts; terminal installation is observed without assuming a command succeeded. */
export const useDaemonStatus = () =>
  useQuery({
    queryKey: daemonStatusKey,
    queryFn: ({ signal }) =>
      unwrap(api.GET("/system/daemon/status", { signal: AbortSignal.any([signal, AbortSignal.timeout(10_000)]) })),
    refetchInterval: 3000,
  });

/** Only the existing web-exposed operations; persistence installation/removal remain CLI-only. */
export function useChangeDaemon() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (action: DaemonAction): Promise<SchemaToggled | SchemaRestarted> => {
      const signal = AbortSignal.timeout(15_000);
      switch (action) {
        case "pause":
          return unwrap(api.POST("/system/daemon/pause", { signal }));
        case "resume":
          return unwrap(api.POST("/system/daemon/resume", { signal }));
        case "restart":
          return unwrap(api.POST("/system/daemon/restart", { signal }));
      }
    },
    retry: false,
    onSuccess: async (answer) => {
      await client.cancelQueries({ queryKey: daemonStatusKey });
      if ("paused" in answer)
        client.setQueryData<SchemaDaemonStatus>(daemonStatusKey, (status) =>
          status ? { ...status, paused: answer.paused } : status,
        );
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: daemonStatusKey });
      void client.invalidateQueries({ queryKey: ["ui", "status"] });
    },
  });
}
