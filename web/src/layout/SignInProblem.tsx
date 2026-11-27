// A connector status that says its sign-in expired: a primary "Sign in to X" button that runs the same step as the
// command the message names (`hz setup google`), with the raw error folded away. Any other problem is shown as it is.
import { runCommand } from "@/components/CommandActions";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";

const SIGN_IN = /\bhz setup ([a-z][a-z0-9-]*)\b/;

/** The `hz setup NAME` step a problem points to, and the name a person knows it by; null for any other problem. */
export function signInStep(problem: string): { command: string; name: string } | null {
  const match = SIGN_IN.exec(problem);
  if (!match || !/sign(ed)?[ -]?in|sign-in|expired|invalid_grant|not connected/i.test(problem)) return null;
  const word = match[1] ?? "";
  return { command: `hz setup ${word}`, name: word.charAt(0).toUpperCase() + word.slice(1) };
}

export function SignInProblem({ problem }: Readonly<{ problem: string }>) {
  const step = signInStep(problem);
  if (!step) return <p>{problem}</p>;
  return (
    <div className="space-y-2">
      <Button onClick={() => runCommand(step.command)}>{t("calendar.signIn", { name: step.name })}</Button>
      <details className="text-[13px] text-muted-foreground">
        <summary className="cursor-pointer">{t("calendar.signInDetails")}</summary>
        <p className="mt-1 break-words">{problem}</p>
      </details>
    </div>
  );
}
