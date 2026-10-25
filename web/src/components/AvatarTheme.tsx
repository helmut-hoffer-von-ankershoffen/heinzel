import { createContext, type ReactNode, useContext } from "react";
import { useThemes } from "@/api/queries";
import type { ThemeLook } from "./heinzel";

const AvatarTheme = createContext<ThemeLook | undefined>(undefined);

/** Theme decoration follows the same selected theme as the app; without a provider the drawing stays original. */
export function AvatarThemeProvider({ children }: { children: ReactNode }) {
  const themes = useThemes();
  const selected = themes.data?.themes.find((theme) => theme.name === themes.data?.selected);
  return <AvatarTheme value={selected?.heinzel ?? undefined}>{children}</AvatarTheme>;
}

export const useAvatarTheme = () => useContext(AvatarTheme);
