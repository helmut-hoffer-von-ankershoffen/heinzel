// What a command button does when the app cannot run the command itself: open the Settings page it sets up, or show
// the command to copy. It is mounted once, in the shell; `runCommand` asks it through an event.
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { useNavigation } from "@/api/queries";
import { CommandDialog } from "@/components/CommandDialog";
import { SHOW_COMMAND } from "@/lib/commandIntent";
import { settingsSectionFor } from "@/lib/commandRoute";

export function CommandHost() {
  const [command, setCommand] = useState<string | null>(null);
  const [, setParams] = useSearchParams();
  const navigation = useNavigation();
  useEffect(() => {
    const show = (event: Event) => {
      const asked = (event as CustomEvent<string>).detail;
      const section = settingsSectionFor(asked);
      if (!section) {
        setCommand(asked);
        return;
      }
      const page = navigation.data?.find((entry) => entry.settings && entry.module === section);
      setParams((current) => {
        const next = new URLSearchParams(current);
        next.set("settings", page?.id ?? section);
        return next;
      });
    };
    window.addEventListener(SHOW_COMMAND, show);
    return () => window.removeEventListener(SHOW_COMMAND, show);
  }, [navigation.data, setParams]);
  return command ? <CommandDialog command={command} onClose={() => setCommand(null)} /> : null;
}
