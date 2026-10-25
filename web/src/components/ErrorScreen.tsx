// The friendly "something went wrong" screen: a few Heinzelmännchen failing to lift a crate, a short warm headline, the
// detail folded away and a way to try again. One component for the React error boundary, a call that failed
// unexpectedly and the page that doesn't exist. The picture is garnish: gone when "playful" is off, still when
// reduced motion is asked for; the words always stay.
import { Component, type ErrorInfo, type ReactNode } from "react";
import { isRouteErrorResponse, useRouteError } from "react-router";
import { usePlayful } from "@/api/queries";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { HeinzelAvatar } from "./HeinzelAvatar";
import type { HeinzelState } from "./heinzel";
import { Button } from "./ui/button";

/** Three Heinzelmännchen heaving at a crate that will not budge; the one in front sets the mood. */
function Scene({ lead }: { lead: HeinzelState }) {
  return (
    <div aria-hidden className="flex items-end justify-center gap-1" data-testid="error-scene">
      <HeinzelAvatar state={lead} age="grown" hat="red" shirt="green" className="h-24 w-14" />
      <svg viewBox="0 0 80 56" className="h-12 w-[4.5rem] shrink-0" role="presentation">
        <rect x="4" y="8" width="72" height="44" rx="6" className="fill-muted stroke-border" strokeWidth="3" />
        <path d="M4 22h72M40 8v44" className="stroke-border" strokeWidth="3" />
      </svg>
      <HeinzelAvatar state="working" age="young" hat="teal" shirt="rose" build="slim" className="h-20 w-12" />
      <HeinzelAvatar state="thinking" age="baby" shirt="ochre" className="h-14 w-9" />
    </div>
  );
}

type Props = {
  /** Without one (the 404 inside a page that has its own heading) the block shows no heading. */
  title?: string;
  /** Announce it as an alert: a failed call, not a page that is simply not there. */
  alert?: boolean;
  /** One warm sentence on what happened and what the person can do. */
  message?: string;
  /** The technical detail, folded away. */
  detail?: string;
  /** "Try again": reload the data, or render the screen again. */
  onRetry?: () => void;
  /** What the picture's lead Heinzelmännchen feels. */
  state?: HeinzelState;
  /** Inside a page, as a block, instead of the whole screen. */
  compact?: boolean;
  children?: ReactNode;
};

export function ErrorScreen({
  title,
  message,
  detail,
  onRetry,
  state = "blocked",
  compact = false,
  alert = false,
  children,
}: Props) {
  const playful = usePlayful();
  const Root = compact ? "div" : "main";
  return (
    <Root
      className={cn(
        "flex flex-col items-center gap-4 text-center",
        compact
          ? cn("rounded-lg border p-6", alert ? "border-bad/40" : "border-border")
          : "mx-auto min-h-svh max-w-xl justify-center p-8",
      )}
    >
      {playful && <Scene lead={state} />}
      {!title ? null : compact ? (
        <h2 className="text-xl font-semibold text-foreground">{title}</h2>
      ) : (
        <h1 className="text-xl font-semibold text-foreground">{title}</h1>
      )}
      {message && (
        <p role={alert ? "alert" : undefined} className="max-w-md text-sm text-muted-foreground">
          {message}
        </p>
      )}
      {children}
      {detail && (
        <details className="w-full max-w-md text-left text-xs text-muted-foreground">
          <summary className="inline-flex min-h-6 cursor-pointer items-center">{t("error.details")}</summary>
          <section
            // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be reachable by keyboard
            tabIndex={0}
            aria-label={t("error.details")}
            className="mt-2 max-h-48 overflow-auto rounded-md border border-border bg-muted p-2 text-foreground"
          >
            <pre className="font-mono whitespace-pre-wrap">{detail}</pre>
          </section>
        </details>
      )}
      {onRetry && <Button onClick={onRetry}>{t("error.retry")}</Button>}
    </Root>
  );
}

/** The router's error element: a screen that threw while rendering, or a response the router turned into an error. */
export function RouteError() {
  const error = useRouteError();
  const detail = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}${error.data ? `\n${String(error.data)}` : ""}`
    : errorDetail(error);
  return (
    <ErrorScreen
      title={t("error.title")}
      message={t("error.message")}
      detail={detail}
      onRetry={() => window.location.reload()}
    />
  );
}

/** What went wrong, as the detail text: the message, and where it happened when React says. */
export function errorDetail(error: unknown, stack?: string | null): string {
  const text = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  return stack ? `${text}\n${stack.trim()}` : text;
}

type BoundaryState = { error: unknown; stack: string | null; failed: boolean };

/** Catches what the router's own error element can't (the providers, the router itself) and shows the same screen. */
export class ErrorBoundary extends Component<{ children: ReactNode }, BoundaryState> {
  state: BoundaryState = { error: null, stack: null, failed: false };
  static getDerivedStateFromError(error: unknown): Partial<BoundaryState> {
    return { error, failed: true };
  }
  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error(error);
    this.setState({ stack: info.componentStack ?? null });
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <ErrorScreen
        title={t("error.title")}
        message={t("error.message")}
        detail={errorDetail(this.state.error, this.state.stack)}
        onRetry={() => this.setState({ error: null, stack: null, failed: false })}
      />
    );
  }
}
