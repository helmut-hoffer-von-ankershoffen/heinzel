// A Heinzelmännchen in a moment of the app (an empty list, a wait, a success, a problem). It is garnish: decorative,
// never instead of the words, and gone when Settings → General has "playful" off (`hz system config set playful false`).
import { usePlayful } from "@/api/queries";
import { cn } from "@/lib/utils";
import { HeinzelAvatar } from "./HeinzelAvatar";
import type { Age, HeinzelState, View } from "./heinzel";

type Props = { state?: HeinzelState; age?: Age; view?: View; className?: string };

export function Gnome({ state = "idle", age = "young", view = "full", className }: Props) {
  const playful = usePlayful();
  if (!playful) return null;
  return (
    <HeinzelAvatar
      state={state}
      age={age}
      view={view}
      className={cn(view === "bust" ? "size-10" : "h-20 w-12", className)}
    />
  );
}
