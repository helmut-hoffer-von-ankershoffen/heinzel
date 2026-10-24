// The left sidebar: home and search on top, the navigation tree in the middle, profile and settings at the bottom.
import { Home, Moon, Search, Settings, Sun, UserRound } from "lucide-react";
import { NavLink } from "react-router";
import { useContracts, useProfile } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { contractPath, sections } from "./navigation";
import { useTheme } from "./theme";

const link = ({ isActive }: { isActive: boolean }) =>
  cn(
    "block truncate rounded-md px-2 py-1 text-sm hover:bg-accent",
    isActive ? "bg-accent font-medium text-foreground" : "text-muted-foreground",
  );

export function Sidebar({ onSearch }: { onSearch: () => void }) {
  const contracts = useContracts();
  const profile = useProfile();
  const [theme, toggleTheme] = useTheme();
  return (
    <div className="flex h-full flex-col gap-2 p-3">
      <div className="flex items-center gap-1">
        <Button asChild variant="ghost" size="sm" className="justify-start">
          <NavLink to="/" end>
            <Home aria-hidden />
            {t("nav.home")}
          </NavLink>
        </Button>
        <Button variant="outline" size="sm" className="ml-auto" onClick={onSearch} aria-keyshortcuts="Meta+K Control+K">
          <Search aria-hidden />
          {t("search.open")}
          <kbd className="ml-1 font-mono text-[10px] text-muted-foreground">⌘K</kbd>
        </Button>
      </div>
      <nav aria-label={t("nav.label")} className="min-h-0 flex-1 overflow-y-auto">
        <ul className="flex flex-col gap-1">
          {sections.map((section) => (
            <li key={section.label}>
              {section.to ? (
                <NavLink to={section.to} end className={link}>
                  {t(section.label)}
                </NavLink>
              ) : (
                <>
                  <span className="mt-2 block px-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                    {t(section.label)}
                  </span>
                  <ul className="ml-2 border-l border-border pl-2">
                    {section.items.map((item) => (
                      <li key={item.to}>
                        <NavLink to={item.to} end className={link}>
                          {t(item.label)}
                        </NavLink>
                      </li>
                    ))}
                    {section.label === "nav.contracts" &&
                      contracts.data?.contracts.map((contract) => {
                        const qualified = `${contract.namespace}:${contract.name}`;
                        return (
                          <li key={qualified}>
                            <NavLink to={contractPath(qualified)} className={link} title={contract.file}>
                              {contract.title ?? contract.name}
                            </NavLink>
                          </li>
                        );
                      })}
                  </ul>
                </>
              )}
            </li>
          ))}
        </ul>
      </nav>
      <div className="flex items-center gap-1 border-t border-border pt-2">
        <Button asChild variant="ghost" size="sm" className="min-w-0 flex-1 justify-start">
          <NavLink to="/profile">
            <UserRound aria-hidden />
            <span className="truncate">
              {profile.data?.handle ?? t("nav.profile")}
              {profile.data?.role && <span className="text-muted-foreground"> · {profile.data.role}</span>}
            </span>
          </NavLink>
        </Button>
        <Button asChild variant="ghost" size="icon" aria-label={t("nav.settings")} title={t("nav.settings")}>
          <NavLink to="/settings">
            <Settings aria-hidden />
          </NavLink>
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleTheme}
          aria-label={t("theme.toggle")}
          title={t("theme.toggle")}
        >
          {theme === "dark" ? <Sun aria-hidden /> : <Moon aria-hidden />}
        </Button>
      </div>
    </div>
  );
}
