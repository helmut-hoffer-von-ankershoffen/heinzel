import type { SchemaContractVersion } from "@/api/client";

/** Show only the contract versions returned by the owning session, including unapproved amendments. */
export function ContractHistory({ contracts }: { contracts: SchemaContractVersion[] }) {
  if (contracts.length === 0) return null;
  return (
    <section aria-label="Agent contract" className="space-y-2 rounded border border-border p-3 text-sm">
      <h3 className="font-medium">Agent contract</h3>
      {contracts.map((contract) => (
        <article key={`${contract.id}-${contract.version}`} className="space-y-1 border-t border-border pt-2">
          <h4 className="font-medium">{contract.title}</h4>
          <p>
            Contract v{contract.version}: {contract.status}
            {contract.parent_version ? ` · amends version ${contract.parent_version}` : ""}
          </p>
          <p className="whitespace-pre-wrap break-words">{contract.text}</p>
          {contract.reason && <p className="whitespace-pre-wrap break-words">{contract.reason}</p>}
          <p className="break-all text-xs text-muted-foreground">Version fingerprint: {contract.sha256}</p>
        </article>
      ))}
    </section>
  );
}
