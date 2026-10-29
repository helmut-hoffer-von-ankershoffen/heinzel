import type { SchemaContractVersion } from "@/api/client";

function Version({ contract }: { contract: SchemaContractVersion }) {
  return (
    <article className="space-y-1 border-t border-border pt-2">
      <h4 className="font-medium">{contract.title}</h4>
      <p>
        Contract v{contract.version}: {contract.status}
        {contract.parent_version ? ` · amends version ${contract.parent_version}` : ""}
        {typeof contract.created_at === "string" && (
          <>
            {" "}
            · <time dateTime={contract.created_at}>{contract.created_at.slice(0, 10)}</time>
          </>
        )}
      </p>
      <details>
        <summary className="cursor-pointer">Review terms</summary>
        <p className="whitespace-pre-wrap break-words">{contract.text}</p>
        {contract.reason && <p className="whitespace-pre-wrap break-words">{contract.reason}</p>}
        <p className="break-all text-xs text-muted-foreground">Version fingerprint: {contract.sha256}</p>
      </details>
    </article>
  );
}

/** Present the latest approved and proposed versions, with earlier versions available on demand. */
export function ContractHistory({ contracts }: { contracts: SchemaContractVersion[] }) {
  if (contracts.length === 0) return null;
  const ordered = [...contracts].sort((a, b) => b.version - a.version);
  const current = [
    ordered.find((item) => item.status === "proposed"),
    ordered.find((item) => item.status === "approved"),
  ].filter((item): item is SchemaContractVersion => item !== undefined);
  const visible = current.length ? current : ordered.slice(0, 1);
  const earlier = ordered.filter((item) => !visible.includes(item));
  return (
    <section aria-label="Agent contract" className="space-y-2 rounded border border-border p-3 text-sm">
      <h3 className="font-medium">Agent contract</h3>
      {visible.map((contract) => (
        <Version key={`${contract.id}-${contract.version}`} contract={contract} />
      ))}
      {earlier.length > 0 && (
        <details>
          <summary className="cursor-pointer">Earlier versions ({earlier.length})</summary>
          {earlier.map((contract) => (
            <Version key={`${contract.id}-${contract.version}`} contract={contract} />
          ))}
        </details>
      )}
    </section>
  );
}
