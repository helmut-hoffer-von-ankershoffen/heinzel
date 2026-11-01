// One query per operation the screens read; TanStack Query caches them, so screens share what they fetched.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isLocalServer } from "@/lib/browser";
import { splash, unreachableError } from "@/splash/store";
import { api, unwrap } from "./client";
import type { components } from "./schema";
import { stream } from "./stream";

export const keys = {
  doctor: ["system", "doctor"],
  config: ["system", "config", "show"],
  profile: ["org", "show"],
  team: ["team", "show"],
  contracts: ["heinzel", "list"],
  graph: ["heinzel", "graph"],
  contract: (name: string) => ["heinzel", "show", name],
  playbooks: ["heinzel", "playbooks"],
  discover: ["heinzel", "discover"],
  jobs: ["system", "daemon", "jobs"],
  expert: ["system", "expert"],
} as const;

export const useDoctor = () => useQuery({ queryKey: keys.doctor, queryFn: () => unwrap(api.GET("/system/doctor")) });
export const useConfig = () =>
  useQuery({ queryKey: keys.config, queryFn: () => unwrap(api.GET("/system/config/show")) });
/** Whether the Heinzelmännchen play along: `hz system config set playful false` (Settings → General) turns them off. */
export function usePlayful(): boolean {
  const config = useConfig();
  const value = config.data?.settings.find((s) => s.section === "hz" && s.key === "playful")?.value;
  return config.data !== undefined && value !== false;
}
export const useProfile = () => useQuery({ queryKey: keys.profile, queryFn: () => unwrap(api.GET("/org/show")) });
export const useTeam = () => useQuery({ queryKey: keys.team, queryFn: () => unwrap(api.GET("/team/show")) });
/** `hz heinzel list`: your Heinzelmännchen, one per contract; the sidebar and search read the same list. */
export const useHeinzelmaennchen = () =>
  useQuery({ queryKey: keys.contracts, queryFn: () => unwrap(api.GET("/heinzel/list")) });
export const useContracts = () =>
  useQuery({ queryKey: keys.contracts, queryFn: () => unwrap(api.GET("/heinzel/list")) });
export const useGraph = () => useQuery({ queryKey: keys.graph, queryFn: () => unwrap(api.GET("/heinzel/graph")) });
export const useContract = (name: string) =>
  useQuery({
    queryKey: keys.contract(name),
    queryFn: () => unwrap(api.GET("/heinzel/show", { params: { query: { name } } })),
  });
export const usePlaybooks = () =>
  useQuery({ queryKey: keys.playbooks, queryFn: () => unwrap(api.GET("/heinzel/playbooks")) });
/** Discovery reads your systems through bound capabilities; it runs when asked, not on every visit. */
export const useDiscover = (enabled: boolean) =>
  useQuery({ queryKey: keys.discover, queryFn: () => unwrap(api.GET("/heinzel/discover")), enabled });
export const useJobs = () => useQuery({ queryKey: keys.jobs, queryFn: () => unwrap(api.GET("/system/daemon/jobs")) });

/** Installed providers own their settings and sidebar entries; an absent provider contributes no navigation. */
export const useNavigation = () =>
  useQuery({
    queryKey: ["ui", "navigation"],
    queryFn: () => unwrap(api.GET("/system/ui/navigation")),
    refetchOnWindowFocus: true,
  });

/** Expert mode and every operation's audience: which screens the navigation lists (ADR 0041). */
export const useExpert = () => useQuery({ queryKey: keys.expert, queryFn: () => unwrap(api.GET("/system/expert")) });
/** `hz system expert on` or `off`, the operations the CLI runs; the answer is the mode in effect. */
export const useSetExpert = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (on: boolean) => unwrap(on ? api.POST("/system/expert/on") : api.POST("/system/expert/off")),
    onSuccess: (mode) => {
      client.setQueryData(keys.expert, mode);
      void client.invalidateQueries({ queryKey: keys.config });
    },
  });
};

export const useAsk = () =>
  useMutation({
    mutationFn: (variables: { prompt: string; budget?: number }) =>
      unwrap(
        api.POST("/system/runner/ask", {
          params: { query: { prompt: variables.prompt, budget_usd: variables.budget ?? null } },
        }),
      ),
  });

/** `hz system config set|unset` in the user's configuration; the settings dialog refreshes from the answer. */
/** A setting's current value from `hz system config show`, as text; "" when unset. */
export function useSetting(section: string, key: string): string {
  const config = useConfig();
  const value = config.data?.settings.find((setting) => setting.section === section && setting.key === key)?.value;
  return typeof value === "string" ? value : "";
}

export const useSetSetting = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (variables: { key: string; value: string | null }) =>
      variables.value === null
        ? unwrap(api.POST("/system/config/unset", { params: { query: { key: variables.key, scope: "user" } } }))
        : unwrap(
            api.POST("/system/config/set", {
              params: { query: { key: variables.key, value: variables.value, scope: "user" } },
            }),
          ),
    onSettled: () => client.invalidateQueries({ queryKey: keys.config }),
  });
};

/** `hz system config reveal <scope>`: the file manager on the hz machine opens at that configuration file. */
export const useRevealConfig = () =>
  useMutation({
    mutationFn: (scope: "site" | "user" | "project") =>
      unwrap(api.POST("/system/config/reveal", { params: { query: { scope } } })),
  });

/** `hz system changelog`: this version's changelog as Markdown, or with `newer` what newer releases bring. */
export const useChangelog = (newer: boolean) =>
  useQuery({
    queryKey: ["system", "changelog", newer],
    queryFn: () => unwrap(api.GET("/system/changelog", { params: { query: { newer } } })),
    staleTime: 60 * 60 * 1000,
  });

export const useInfo = () =>
  useQuery({ queryKey: ["system", "info"], queryFn: () => unwrap(api.GET("/system/info")), staleTime: 10 * 60 * 1000 });

/** `hz system theme list`: every theme the plugins offer, the selected one first in effect (ADR 0044). */
export const useThemes = () =>
  useQuery({ queryKey: ["system", "theme", "list"], queryFn: () => unwrap(api.GET("/system/theme/list")) });

/** `hz system theme signals`: the daypart and the weather the theme's variants follow, asked again every minute. */
export const useThemeSignals = () =>
  useQuery({
    queryKey: ["system", "theme", "signals"],
    queryFn: () => unwrap(api.GET("/system/theme/signals")),
    refetchInterval: (query) => (query.state.data?.refresh ?? 60) * 1000,
  });

/** `hz system theme use <name>`; the app applies it as soon as the list is read again. */
export const useSetTheme = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => unwrap(api.POST("/system/theme/use", { params: { query: { name } } })),
    onSettled: () => client.invalidateQueries({ queryKey: ["system", "theme", "list"] }),
  });
};

/** `hz ai access list`: every AI access option, its status and the one in use (ADR 0057). */
export const useAiAccess = () =>
  useQuery({ queryKey: ["ai", "access", "list"], queryFn: () => unwrap(api.GET("/ai/access/list")) });

/** `hz ai access use <name>`: the one choice, like the theme; it binds the runner the option names too. */
export const useSetAiAccess = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => unwrap(api.POST("/ai/access/use", { params: { query: { name } } })),
    onSettled: () => client.invalidateQueries({ queryKey: ["ai", "access", "list"] }),
  });
};

/** `hz ai access test --name <name>`: one cheap live check, only when asked; it may cost a little. */
export const useTestAiAccess = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => unwrap(api.POST("/ai/access/test", { params: { query: { name } } })),
    onSettled: () => client.invalidateQueries({ queryKey: ["ai", "access", "list"] }),
  });
};
/** `hz ai models`: the models and efforts of the AI access in use, for the chat's picker (ADR 0057). */
export const useAiModels = () =>
  useQuery({ queryKey: ["ai", "models"], queryFn: () => unwrap(api.GET("/ai/models")), staleTime: 5 * 60 * 1000 });

/** `hz system notify list`: the latest notices, newest first as the store keeps them (ADR 0043). */
export const useNotices = () =>
  useQuery({
    queryKey: ["system", "notify", "list"],
    queryFn: () => unwrap(api.GET("/system/notify/list", { params: { query: { limit: 200 } } })),
    refetchInterval: 30_000,
  });

/** `hz system notify test`: an urgent test notice the open app or the daemon pops up. */
export const useTestNotice = () => {
  const client = useQueryClient();
  return useMutation({
    // A remote browser tests its own notifications, not the server's desktop.
    mutationFn: () => unwrap(api.POST("/system/notify/test", { params: { query: { native: isLocalServer() } } })),
    onSettled: () => client.invalidateQueries({ queryKey: ["system", "notify", "list"] }),
  });
};

/** `hz system notify settings`: System Settings > Notifications opens on the machine hz runs on. */
export const useOpenNotificationSettings = () =>
  useMutation({ mutationFn: () => unwrap(api.POST("/system/notify/settings")) });

/** `hz system capability list`: which adapter serves which capability (`[capabilities]`), for the Connectors page. */
export const useCapabilities = () =>
  useQuery({ queryKey: ["system", "capability", "list"], queryFn: () => unwrap(api.GET("/system/capability/list")) });

/** `hz system plugin list` and `search`: what's installed, and what can be. */
export const usePlugins = () =>
  useQuery({
    queryKey: ["system", "plugin", "list"],
    queryFn: ({ signal }) => unwrap(api.GET("/system/plugin/list", { params: { query: { fresh: true } }, signal })),
  });
/** Plugins changed on this machine since this server started, however they changed (the CLI, another window). */
export const usePluginsPending = () =>
  useQuery({
    queryKey: ["system", "plugin", "pending"],
    queryFn: ({ signal }) => unwrap(api.GET("/system/plugin/pending", { signal })),
    // In-process on the server, so cheap; still no poll of a hidden page (the default), a slow one while it is shown,
    // and a check when the window gets focus or becomes visible again.
    refetchInterval: 5 * 60_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    staleTime: 30_000,
    retry: false,
  });
export const usePluginSearch = (text: string) =>
  useQuery({
    queryKey: ["system", "plugin", "search", text],
    queryFn: ({ signal }) =>
      unwrap(api.GET("/system/plugin/search", { params: { query: { text: text || undefined } }, signal })),
    staleTime: 5 * 60 * 1000,
  });

/** `hz system plugin add` without `confirm`: what a spec would install (source, version, publisher), installing nothing. */
export const usePluginPreview = () =>
  useMutation({
    mutationFn: (spec: string) => unwrap(api.POST("/system/plugin/add", { params: { query: { spec } } })),
  });

/** Poll only while a confirmed install is active; the server receipt is authoritative across reloads. */
export const usePluginOperation = (operationId: string | null) =>
  useQuery({
    queryKey: ["system", "plugin", "operation", operationId],
    queryFn: ({ signal }) =>
      unwrap(api.GET("/system/plugin/operation", { params: { query: { operation_id: operationId ?? "" } }, signal })),
    enabled: Boolean(operationId),
    refetchInterval: (query) => (query.state.data?.status === "running" ? 1000 : false),
    retry: false,
  });

export const readPluginOperation = async (operationId: string) =>
  unwrap(api.GET("/system/plugin/operation", { params: { query: { operation_id: operationId } } }));

export type PluginChangeRequest = (
  | { add: string; operationId: string }
  | { remove: string }
  | { enable: string }
  | { disable: string }
) & { signal?: AbortSignal; onProgress?: (text: string) => void };

/** `hz system plugin add --confirm|remove|enable|disable`: changes what code runs, so the UI asks first (ADR 0047). */
export const useChangePlugin = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (variables: PluginChangeRequest) => {
      if ("add" in variables) {
        // The requirement the preview answered, pinned to what was shown, so the dialog's version is the one installed.
        // Installing rebuilds the uv tool environment and can take minutes. The authenticated operation stream sends
        // its task event immediately, keeps quiet work alive across proxies, and returns the same result or error.
        return stream<components["schemas"]["PluginChange"]>(
          "/system/plugin/add",
          { spec: variables.add, confirm: true, operation_id: variables.operationId },
          (event) => {
            if (event.event === "task" && typeof event.data.text === "string") variables.onProgress?.(event.data.text);
          },
          variables.signal,
        );
      }
      if ("remove" in variables) {
        // Removal also rebuilds the tool and can outlast a normal request or a proxy's quiet-work timeout.
        return stream<components["schemas"]["PluginChange"]>(
          "/system/plugin/remove",
          { distribution: variables.remove },
          (event) => {
            if (event.event === "task" && typeof event.data.text === "string") variables.onProgress?.(event.data.text);
          },
          variables.signal,
        );
      }
      if ("enable" in variables) {
        return unwrap(
          api.POST("/system/plugin/enable", {
            params: { query: { distribution: variables.enable } },
            signal: variables.signal,
          }),
        );
      }
      return unwrap(
        api.POST("/system/plugin/disable", {
          params: { query: { distribution: variables.disable } },
          signal: variables.signal,
        }),
      );
    },
    onSuccess: async (answer) => {
      if (answer.operation_id && answer.candidate) {
        client.setQueryData(["system", "plugin", "operation", answer.operation_id], {
          operation_id: answer.operation_id,
          status: answer.done ? "completed" : "unknown",
          candidate: answer.candidate,
          result: answer,
        });
      }
      if (!answer.done) return;
      // A query started before the installation must not replace its authoritative after-state.
      await client.cancelQueries({ queryKey: ["system", "plugin"] });
      client.setQueryData(["system", "plugin", "list"], answer.plugins);
      const installed = new Set(answer.plugins.map((plugin) => pluginIdentity(plugin.distribution)));
      client.setQueriesData<components["schemas"]["PluginSearch"]>(
        { queryKey: ["system", "plugin", "search"] },
        (current) =>
          current
            ? {
                ...current,
                plugins: current.plugins.map((plugin) => ({
                  ...plugin,
                  installed: installed.has(pluginIdentity(plugin.name)),
                })),
              }
            : current,
      );
      void client.invalidateQueries({ queryKey: ["system", "plugin"] });
      void client.invalidateQueries({ queryKey: ["ui"] });
    },
    onError: (_error, variables) => {
      // A lost response is uncertain; reconcile reads without replaying an installation.
      void client.invalidateQueries({ queryKey: ["system", "plugin"] });
      if ("add" in variables && variables.operationId)
        void client.invalidateQueries({ queryKey: ["system", "plugin", "operation", variables.operationId] });
    },
  });
};

/** PEP 503 distribution identity; presentation casing and separators do not identify a different installation. */
export const pluginIdentity = (name: string) => name.toLowerCase().replace(/[-_.]+/g, "-");

/** Wait until `hz ui` answers again after a restart: `/system/info` with the same token, every `interval` ms. */
export async function waitForServer(interval = 500, attempts = 120): Promise<boolean> {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, interval));
    try {
      const { response } = await api.GET("/system/info");
      if (response.ok) return true;
    } catch {
      // Not listening yet: the restarting process hasn't taken over the socket's backlog.
    }
  }
  return false;
}

/** `POST /api/restart`: `hz ui` runs itself again with the same port and token; then the page reloads. */
export const useRestartServer = ({
  reload = () => window.location.reload(),
  interval = 500,
}: {
  reload?: () => void;
  interval?: number;
} = {}) =>
  useMutation({
    mutationFn: async () => {
      // Over the app from the moment it is asked, until the reload brings it back.
      splash.send({ type: "restart", at: performance.now() });
      try {
        await unwrap(api.POST("/restart"));
      } catch (error) {
        // The connection dropped as the process replaced itself: it is restarting all the same.
        if (!unreachableError(error)) {
          splash.send({ type: "restart-refused" });
          throw error;
        }
      }
      if (!(await waitForServer(interval))) {
        splash.send({ type: "restart-failed" });
        throw new Error("hz ui didn't answer again; start it in a terminal: hz ui");
      }
      reload();
    },
  });
