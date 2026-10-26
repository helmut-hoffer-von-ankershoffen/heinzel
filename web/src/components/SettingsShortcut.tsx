// Contextual Settings links use one compact, labelled target and keep the current screen and panel open.
import { Settings2 } from "lucide-react";
import { Link, useLocation, useSearchParams } from "react-router";
import { cn } from "@/lib/utils";

export function SettingsShortcut({
  section,
  label,
  onClick,
  className,
}: {
  section: string;
  label: string;
  onClick?: () => void;
  className?: string;
}) {
  const location = useLocation();
  const [current] = useSearchParams();
  const params = new URLSearchParams(current);
  params.set("settings", section);
  params.delete("q");
  return (
    <Link
      to={{ pathname: location.pathname, search: `?${params}`, hash: location.hash }}
      title={label}
      aria-label={label}
      onClick={onClick}
      className={cn(
        "inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground",
        className,
      )}
    >
      <Settings2 aria-hidden className="size-4" />
    </Link>
  );
}
