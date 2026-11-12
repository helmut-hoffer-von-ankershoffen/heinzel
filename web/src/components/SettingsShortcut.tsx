// Contextual Settings links use one compact, labelled target and keep the current screen and panel open.
import { Settings2 } from "lucide-react";
import { useLocation, useSearchParams } from "react-router";
import { PaneAction } from "@/components/PaneActions";

/** The in-app address of a settings section, from wherever the person is (for a `PaneAction to=`). */
export function useSettingsTarget(section: string): string {
  const location = useLocation();
  const [current] = useSearchParams();
  const params = new URLSearchParams(current);
  params.set("settings", section);
  params.delete("q");
  return `${location.pathname}?${params}${location.hash}`;
}

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
  // A ghost icon action like every other pane chrome: the same size, focus ring and tooltip.
  return (
    <PaneAction ghost icon={Settings2} to={useSettingsTarget(section)} onClick={onClick} className={className}>
      {label}
    </PaneAction>
  );
}
