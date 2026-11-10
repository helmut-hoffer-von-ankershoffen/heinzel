import { type ReactNode, useEffect, useRef, useState } from "react";
import { t } from "@/i18n";

/** Ordinary browser links receive only a trusted-click handoff in the isolated native shell. */
export function PublicMediaLink({
  href,
  label,
  className,
  children,
}: {
  href: string;
  label?: string;
  className?: string;
  children: ReactNode;
}) {
  const anchor = useRef<HTMLAnchorElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const element = anchor.current;
    const result = (event: Event) => {
      if (event instanceof CustomEvent && event.detail === false) setFailed(true);
    };
    element?.addEventListener("hz-public-media-result", result);
    return () => element?.removeEventListener("hz-public-media-result", result);
  }, []);
  return (
    <>
      <a
        ref={anchor}
        href={href}
        aria-label={label}
        className={className}
        data-hz-public-media=""
        target="_blank"
        rel="noreferrer noopener"
      >
        {children}
      </a>
      {failed && (
        <p role="alert" className="text-sm text-muted-foreground">
          {t("plugins.mediaOpenFailed")}
        </p>
      )}
    </>
  );
}
