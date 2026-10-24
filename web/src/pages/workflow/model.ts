// The workflow hz knows of, as one graph: the systems it reads (capabilities), the asset types playbooks keep and
// the assets discovery found, the contracts (yours, and templates playbooks suggest) and the outputs they maintain.
// Pure functions of the API's answers, so the screen and its tests build the same graph.
import type {
  SchemaCandidate,
  SchemaCapabilityInfo,
  SchemaContractGraph,
  SchemaContractSummary,
  SchemaDiscovery,
  SchemaPlaybookView,
  SchemaTemplateView,
} from "@/api/client";

export type WorkflowKind = "source" | "assetType" | "asset" | "contract" | "template" | "output";

type Base = { id: string; label: string; sublabel?: string };
export type WorkflowNode =
  | (Base & { kind: "source"; capability: SchemaCapabilityInfo | null; name: string; reads: string[] })
  | (Base & {
      kind: "assetType";
      playbook: SchemaPlaybookView;
      asset: SchemaPlaybookView["assets"][number];
      found: number;
    })
  | (Base & { kind: "asset"; candidate: SchemaCandidate })
  | (Base & {
      kind: "template";
      playbook: SchemaPlaybookView;
      template: SchemaTemplateView;
      command: string;
    })
  | (Base & { kind: "contract"; qualified: string; summary: SchemaContractSummary | null; errors: number })
  | (Base & { kind: "output"; ref: string; maintained: boolean; suggested: boolean; contract: string | null });

export type WorkflowEdge = {
  id: string;
  source: string;
  target: string;
  /** `reads`: the target reads the source; `finds`: discovery finds the target through the source; `maintains`. */
  kind: "reads" | "finds" | "maintains";
  label?: string;
  /** Part of a suggestion (a template), not of anything that exists yet. */
  suggested: boolean;
};

export type WorkflowGraph = { nodes: WorkflowNode[]; edges: WorkflowEdge[] };

export type WorkflowInputs = {
  capabilities?: SchemaCapabilityInfo[];
  playbooks?: SchemaPlaybookView[];
  graph?: SchemaContractGraph;
  contracts?: SchemaContractSummary[];
  discovery?: SchemaDiscovery;
};

/** The capability a read method or a graph source belongs to: `tracker.issues(project=X)` is `tracker`. */
export function capabilityOf(read: string): string {
  return read.split(/[.(]/, 1)[0] ?? read;
}

/** A shell word as `runsAsIs` accepts it: plain, or single-quoted. */
export function shellWord(value: string): string {
  return /^[\w./:=@,+-]+$/.test(value) ? value : `'${value.replaceAll("'", "'\\''")}'`;
}

/** The template's command, with `--asset <type>=<ref>` for each asset type discovery found exactly one of. */
export function templateCommand(template: SchemaTemplateView, candidates: SchemaCandidate[]): string {
  const filled = template.assets.flatMap((type) => {
    const found = candidates.filter((candidate) => candidate.asset === type);
    return found.length === 1 && found[0] ? [`--asset ${shellWord(`${type}=${found[0].ref}`)}`] : [];
  });
  return [template.command, ...filled].join(" ");
}

const IMPLIED = new Set(["agent-runner"]);

export function buildWorkflow({
  capabilities = [],
  playbooks = [],
  graph,
  contracts = [],
  discovery,
}: WorkflowInputs): WorkflowGraph {
  const nodes = new Map<string, WorkflowNode>();
  const edges = new Map<string, WorkflowEdge>();
  const edge = (source: string, target: string, kind: WorkflowEdge["kind"], suggested: boolean, label?: string) => {
    const id = `${source}->${target}`;
    if (source === target || edges.has(id)) return;
    edges.set(id, { id, source, target, kind, suggested, label });
  };
  const bound = new Map(capabilities.map((capability) => [capability.name, capability]));
  const source = (name: string, read?: string): string => {
    const id = `cap:${name}`;
    const existing = nodes.get(id);
    if (existing?.kind === "source") {
      if (read && !existing.reads.includes(read)) existing.reads.push(read);
    } else {
      const capability = bound.get(name) ?? null;
      nodes.set(id, {
        id,
        kind: "source",
        name,
        label: name,
        sublabel: capability?.bound ?? undefined,
        capability,
        reads: read ? [read] : [],
      });
    }
    return id;
  };

  // The systems you connected, whether anything reads them yet or not.
  for (const capability of capabilities) {
    if (capability.bound && !IMPLIED.has(capability.name)) source(capability.name);
  }

  // Existing contracts: solid. Graph sources fold into their capability; assets are outputs.
  const summaries = new Map(contracts.map((summary) => [`${summary.namespace}:${summary.name}`, summary]));
  const graphIds = new Map<string, string>();
  for (const node of graph?.nodes ?? []) {
    if (node.kind === "source") {
      graphIds.set(node.id, source(capabilityOf(node.id), node.id));
    } else if (node.kind === "contract") {
      const summary = summaries.get(node.id) ?? null;
      const id = `contract:${node.id}`;
      graphIds.set(node.id, id);
      nodes.set(id, {
        id,
        kind: "contract",
        qualified: node.id,
        label: summary?.title ?? node.id.split(":").at(-1) ?? node.id,
        sublabel: summary?.schedule ?? undefined,
        summary,
        errors: summary?.errors ?? 0,
      });
    } else {
      const id = `out:${node.id}`;
      graphIds.set(node.id, id);
      const contract = /^contract:([^/]+)\/([^#]+)#/.exec(node.id);
      nodes.set(id, {
        id,
        kind: "output",
        ref: node.id,
        label: node.id.split("#").at(-1) ?? node.id,
        sublabel: contract ? `${contract[1]}:${contract[2]}` : undefined,
        maintained: node.maintained,
        suggested: false,
        contract: contract ? `${contract[1]}:${contract[2]}` : null,
      });
    }
  }
  for (const item of graph?.edges ?? []) {
    const from = graphIds.get(item.source);
    const to = graphIds.get(item.target);
    if (from && to) edge(from, to, item.kind, false, item.input ?? undefined);
  }
  // Contracts `hz workflow list` has and the graph lacks (one with errors, say) still belong on it.
  for (const [qualified, summary] of summaries) {
    const id = `contract:${qualified}`;
    if (!nodes.has(id)) {
      nodes.set(id, {
        id,
        kind: "contract",
        qualified,
        label: summary.title ?? summary.name,
        sublabel: summary.schedule ?? undefined,
        summary,
        errors: summary.errors,
      });
    }
  }
  const existingNames = new Set([...summaries.values()].map((summary) => summary.name));

  // Playbooks: asset types, found through their hints' capabilities; templates as suggestions.
  const candidates = discovery?.candidates ?? [];
  for (const playbook of playbooks) {
    const typeIds = new Map<string, string>();
    for (const asset of playbook.assets) {
      const id = `type:${playbook.module}/${asset.name}`;
      typeIds.set(asset.name, id);
      const found = candidates.filter((item) => item.playbook === playbook.module && item.asset === asset.name);
      nodes.set(id, {
        id,
        kind: "assetType",
        playbook,
        asset,
        found: found.length,
        label: asset.name,
        sublabel: playbook.module,
      });
      const hinted = new Set(asset.hints.map((hint) => capabilityOf(hint.read)));
      for (const hint of asset.hints) source(capabilityOf(hint.read), hint.read);
      for (const candidate of found) {
        const candidateId = `asset:${candidate.asset}/${candidate.ref}`;
        nodes.set(candidateId, {
          id: candidateId,
          kind: "asset",
          candidate,
          label: candidate.title,
          sublabel: candidate.ref,
        });
        const through = capabilityOf(candidate.hint);
        hinted.delete(through);
        edge(source(through, candidate.hint), candidateId, "finds", false);
        edge(candidateId, id, "finds", false);
      }
      for (const name of hinted) edge(`cap:${name}`, id, "finds", false);
    }
    for (const template of playbook.templates) {
      const name = template.template.split(":").at(-1) ?? template.template;
      // A contract by the template's name is (most likely) that template, written: no need to suggest it again.
      if (existingNames.has(name)) continue;
      const id = `template:${template.template}`;
      nodes.set(id, {
        id,
        kind: "template",
        playbook,
        template,
        command: templateCommand(template, candidates),
        label: name,
        sublabel: playbook.module,
      });
      for (const type of template.assets) {
        const typeId = typeIds.get(type);
        if (typeId) edge(typeId, id, "reads", true);
      }
      if (template.keeps.length > 0 || template.assets.length === 0) {
        const outId = `out:template:${template.template}`;
        nodes.set(outId, {
          id: outId,
          kind: "output",
          ref: template.template,
          label: template.keeps.join(", ") || name,
          sublabel: name,
          maintained: true,
          suggested: true,
          contract: null,
        });
        edge(id, outId, "maintains", true);
      }
    }
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()] };
}

/** Whether the graph has nothing in it that a person would call a workflow yet. */
export function isEmpty(graph: WorkflowGraph): boolean {
  return graph.nodes.every((node) => node.kind === "source");
}
