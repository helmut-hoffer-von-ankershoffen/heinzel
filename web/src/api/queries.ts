// One query per operation the screens read; TanStack Query caches them, so screens share what they fetched.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
