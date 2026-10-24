// One query per operation the screens read; TanStack Query caches them, so screens share what they fetched.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { splash, unreachableError } from "@/splash/store";
import { api, unwrap } from "./client";

export const keys = {
  doctor: ["system", "doctor"],
  config: ["system", "config", "show"],
  profile: ["org", "show"],
  team: ["team", "show"],
  contracts: ["workflow", "list"],
  graph: ["workflow", "graph"],
  contract: (name: string) => ["workflow", "show", name],
  playbooks: ["workflow", "playbooks"],
  discover: ["workflow", "discover"],
  jobs: ["system", "daemon", "jobs"],
  expert: ["system", "expert"],
} as const;

export const useDoctor = () => useQuery({ queryKey: keys.doctor, queryFn: () => unwrap(api.GET("/system/doctor")) });
export const useConfig = () =>
  useQuery({ queryKey: keys.config, queryFn: () => unwrap(api.GET("/system/config/show")) });
export const useProfile = () => useQuery({ queryKey: keys.profile, queryFn: () => unwrap(api.GET("/org/show")) });
export const useTeam = () => useQuery({ queryKey: keys.team, queryFn: () => unwrap(api.GET("/team/show")) });
export const useContracts = () =>
  useQuery({ queryKey: keys.contracts, queryFn: () => unwrap(api.GET("/workflow/list")) });
export const useGraph = () => useQuery({ queryKey: keys.graph, queryFn: () => unwrap(api.GET("/workflow/graph")) });
export const useContract = (name: string) =>
  useQuery({
    queryKey: keys.contract(name),
    queryFn: () => unwrap(api.GET("/workflow/show", { params: { query: { name } } })),
  });
export const usePlaybooks = () =>
  useQuery({ queryKey: keys.playbooks, queryFn: () => unwrap(api.GET("/workflow/playbooks")) });
/** Discovery reads your systems through bound capabilities; it runs when asked, not on every visit. */
export const useDiscover = (enabled: boolean) =>
  useQuery({ queryKey: keys.discover, queryFn: () => unwrap(api.GET("/workflow/discover")), enabled });
export const useJobs = () => useQuery({ queryKey: keys.jobs, queryFn: () => unwrap(api.GET("/system/daemon/jobs")) });

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

/** `hz system theme use <name>`; the app applies it as soon as the list is read again. */
export const useSetTheme = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => unwrap(api.POST("/system/theme/use", { params: { query: { name } } })),
    onSettled: () => client.invalidateQueries({ queryKey: ["system", "theme", "list"] }),
  });
};

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
    // Native too, as a person pressed the button: the answer says how each way of reaching you stands.
    mutationFn: () => unwrap(api.POST("/system/notify/test", { params: { query: { native: true } } })),
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
  useQuery({ queryKey: ["system", "plugin", "list"], queryFn: () => unwrap(api.GET("/system/plugin/list")) });
export const usePluginSearch = (text: string) =>
  useQuery({
    queryKey: ["system", "plugin", "search", text],
    queryFn: () => unwrap(api.GET("/system/plugin/search", { params: { query: { text: text || undefined } } })),
    staleTime: 5 * 60 * 1000,
  });

/** `hz system plugin add` without `confirm`: what a spec would install (source, version, publisher), installing nothing. */
export const usePluginPreview = () =>
  useMutation({
    mutationFn: (spec: string) => unwrap(api.POST("/system/plugin/add", { params: { query: { spec } } })),
  });

export type PluginChangeRequest = { add: string } | { remove: string } | { enable: string } | { disable: string };

/** `hz system plugin add --confirm|remove|enable|disable`: changes what code runs, so the UI asks first (ADR 0047). */
export const useChangePlugin = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (variables: PluginChangeRequest) => {
      if ("add" in variables) {
        // The requirement the preview answered, pinned to what was shown, so the dialog's version is the one installed.
        return unwrap(api.POST("/system/plugin/add", { params: { query: { spec: variables.add, confirm: true } } }));
      }
      if ("remove" in variables) {
        return unwrap(api.POST("/system/plugin/remove", { params: { query: { distribution: variables.remove } } }));
      }
      if ("enable" in variables) {
        return unwrap(api.POST("/system/plugin/enable", { params: { query: { distribution: variables.enable } } }));
      }
      return unwrap(api.POST("/system/plugin/disable", { params: { query: { distribution: variables.disable } } }));
    },
    onSettled: () => client.invalidateQueries({ queryKey: ["system", "plugin"] }),
  });
};

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
