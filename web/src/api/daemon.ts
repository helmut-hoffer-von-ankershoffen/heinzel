import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type SchemaDaemonStatus,
  type SchemaInstalled,
  type SchemaRestarted,
  type SchemaToggled,
  type SchemaUninstalled,
  unwrap,
} from "./client";

export const daemonStatusKey = ["system", "daemon", "status"] as const;
export type DaemonAction = "install" | "uninstall" | "pause" | "resume" | "restart";

/** The daemon's current facts; refreshed after every confirmed change. */
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
    mutationFn: async (
      action: DaemonAction,
    ): Promise<SchemaInstalled | SchemaUninstalled | SchemaToggled | SchemaRestarted> => {
      const signal = AbortSignal.timeout(30_000);
      switch (action) {
        case "install":
          return unwrap(api.POST("/system/daemon/install", { signal }));
        case "uninstall":
          return unwrap(api.POST("/system/daemon/uninstall", { signal }));
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
    onSettled: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: daemonStatusKey, refetchType: "all" }),
        client.invalidateQueries({ queryKey: ["ui", "status"], refetchType: "all" }),
      ]);
    },
  });
}
