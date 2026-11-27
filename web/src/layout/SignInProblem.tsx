import { useConnectorReport } from "@/api/queries";
import { t } from "@/i18n";
import { ConnectorSignIn } from "./ConnectorSignIn";

export function SignInProblem({ problem }: Readonly<{ problem: string }>) {
  // Error text only decides whether to offer help. The server's binding and provider report decide who can sign in.
  const expired =
    /\bhz setup [a-z][a-z0-9-]*\b/.test(problem) && /sign(ed)?[ -]?in|expired|invalid_grant/i.test(problem);
  const report = useConnectorReport(expired);
  const connector = expired
    ? report.data?.connectors.find(
        (entry) =>
          entry.sign_in &&
          entry.capabilities.some((capability) => capability.capability === "calendar" && capability.used),
      )
    : undefined;
  if (!connector) return <p>{problem}</p>;
  return (
    <div className="space-y-2">
      <ConnectorSignIn name={connector.name} title={connector.title} />
      <details className="text-[13px] text-muted-foreground">
        <summary className="cursor-pointer">{t("calendar.signInDetails")}</summary>
        <p className="mt-1 break-words">{problem}</p>
      </details>
    </div>
  );
}
