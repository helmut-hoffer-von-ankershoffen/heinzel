// The left sidebar: home and search on top, the navigation tree in the middle, profile and settings at the bottom.
// Collapsed, it becomes a rail of icons with the same screens in the same order.

import {
  ChevronDown,
  CircleHelp,
  ExternalLink,
  FileClock,
  GraduationCap,
  Home,
  Info,
  type LucideIcon,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  Sparkles,
  Stethoscope,
  Sun,
  UserRound,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router";
import { useContracts, useExpert, useProfile, useSetExpert } from "@/api/queries";
import { StatusItems } from "@/components/Contributions";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { InstallApp } from "@/pwa/InstallApp";
import { SoundButton } from "@/sound/SoundControls";
import { Branding } from "./Branding";
import { BellButton } from "./NotificationCenter";
import { CONTRACTS_SECTION, contractPath, railItems, sections, visibleSections } from "./navigation";
import { useDark, useSelectedTheme } from "./ThemeTokens";
import { useTheme } from "./theme";
import { NATIVE_SIDEBAR, ZoneTicker } from "./ZoneTicker";

const link = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex items-center gap-3 truncate rounded-lg px-2.5 h-(--row) text-ui text-foreground/90 transition-colors hover:bg-accent/70 hover:text-foreground [&_svg]:size-(--icon) [&_svg]:shrink-0 [&_svg]:stroke-[1.75] [&_svg]:text-icon",
    isActive && "bg-accent text-accent-foreground [&_svg]:text-accent-foreground",
  );

const topButton = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex size-9 items-center justify-center rounded-lg text-icon transition-colors hover:bg-accent/70 [&_svg]:size-(--icon) [&_svg]:stroke-[1.75]",
    isActive && "bg-accent text-accent-foreground",
  );

const railLink = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex size-9 items-center justify-center rounded-lg text-icon transition-colors hover:bg-accent/70 [&_svg]:size-(--icon) [&_svg]:stroke-[1.75]",
    isActive && "bg-accent text-accent-foreground",
  );

/** A line of the sidebar with a full-width hairline under it, like the account line's above it. */
const HAIRLINE_BELOW = "-mx-2 flex shrink-0 items-center gap-1 border-b-[0.5px] border-foreground/20 px-3";

type Props = { collapsed: boolean; onToggle: () => void; onSearch: () => void; onNotifications: () => void };

export function Sidebar({ collapsed, onToggle, onSearch, onNotifications }: Props) {
  return collapsed ? (
    <Rail onToggle={onToggle} onSearch={onSearch} onNotifications={onNotifications} />
  ) : (
    <Full onToggle={onToggle} onSearch={onSearch} onNotifications={onNotifications} />
  );
}

function Full({ onToggle, onSearch, onNotifications }: Omit<Props, "collapsed">) {
  const contracts = useContracts();
  const expert = useExpert();
  const [folded, toggle] = useFolded();
  const listedSections = visibleSections(sections, expert.data).filter(
    // The contracts section stays for its entries; with no graph listed and no contract, there's nothing to show.
    (section) =>
      section.label !== CONTRACTS_SECTION ||
      section.items.length > 0 ||
      Boolean(contracts.data?.heinzelmaennchen.length),
  );
  return (
    <div className="flex h-full flex-col px-2">
      {/* The same height and hairline as the main column's top bar, so both lines run across the window. */}
      <div className={cn(HAIRLINE_BELOW, "h-12")}>
        <ThemeLogo />
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggle}
          aria-expanded
          aria-label={t("nav.collapse")}
          title={t("nav.collapse")}
          className="ml-auto text-icon"
        >
          <PanelLeftClose aria-hidden />
        </Button>
      </div>
      <div className={cn(HAIRLINE_BELOW, "h-12 justify-evenly")}>
        <NavLink to="/" end className={topButton} aria-label={t("nav.home")} title={t("nav.home")}>
          <Home aria-hidden />
        </NavLink>
        <BellButton onOpen={onNotifications} className={topButton({ isActive: false })} />
        <button
          type="button"
          onClick={onSearch}
          aria-keyshortcuts="Meta+K Control+K"
          aria-label={t("search.open")}
          title={`${t("search.open")} (⌘K)`}
          className={topButton({ isActive: false })}
        >
          <Search aria-hidden />
        </button>
      </div>
      <nav aria-label={t("nav.label")} className="mt-2 min-h-0 flex-1 overflow-y-auto">
        <ul className="flex flex-col gap-0.5">
          {listedSections.map((section) => (
            <li key={section.label}>
              {section.to ? (
                <NavLink to={section.to} end className={link}>
                  {section.icon && <section.icon aria-hidden />}
                  {t(section.label)}
                </NavLink>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() => toggle(section.label)}
                    aria-expanded={!folded.has(section.label)}
                    className="group mt-5 mb-1.5 flex items-center gap-1 rounded-md px-2.5 text-ui-sm text-muted-foreground hover:text-foreground"
                  >
                    {t(section.label)}
                    <ChevronDown
                      aria-hidden
                      className={cn("size-3 transition-transform", folded.has(section.label) && "-rotate-90")}
                    />
                  </button>
                  <ul className={cn("flex flex-col gap-0.5", folded.has(section.label) && "hidden")}>
                    {section.items.map((item) => (
                      <li key={item.to}>
                        <NavLink to={item.to} end className={link}>
                          <item.icon aria-hidden />
                          {t(item.label)}
                        </NavLink>
                      </li>
                    ))}
                    {section.label === CONTRACTS_SECTION &&
                      contracts.data?.heinzelmaennchen.map((contract) => {
                        const qualified = `${contract.namespace}:${contract.name}`;
                        return (
                          <li key={qualified}>
                            <NavLink to={contractPath(qualified)} className={link} title={contract.file}>
                              <span aria-hidden className="size-(--icon) shrink-0" />
                              <span className="truncate">{contract.title ?? contract.name}</span>
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
      <ZoneTicker />
      <StatusItems slot="sidebar" className="pb-1" except={NATIVE_SIDEBAR} />
      <Footer />
    </div>
  );
}

const FOLDED = "hz.nav.folded";

/** The navigation sections folded away, remembered in this browser; storage may be unavailable, which is fine. */
function useFolded(): [Set<string>, (label: string) => void] {
  const [folded, setFolded] = useState<Set<string>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem(FOLDED) ?? "[]") as string[]);
    } catch {
      return new Set();
    }
  });
  const toggle = (label: string) =>
    setFolded((current) => {
      const next = new Set(current);
      if (!next.delete(label)) next.add(label);
      try {
        localStorage.setItem(FOLDED, JSON.stringify([...next]));
      } catch {
        // Not remembered, still folded for now.
      }
      return next;
    });
  return [folded, toggle];
}

function Rail({ onToggle, onSearch, onNotifications }: Omit<Props, "collapsed">) {
  const expert = useExpert();
  return (
    <div className="flex h-full flex-col items-center">
      <div className="flex h-12 w-full shrink-0 items-center justify-center border-b-[0.5px] border-foreground/20">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={false}
          aria-label={t("nav.expand")}
          title={t("nav.expand")}
          className="group flex size-9 items-center justify-center rounded-lg text-icon transition-colors hover:bg-accent/70 [&_svg]:size-(--icon)"
        >
          {/* The theme's mark until the pointer or focus is on it; then what a click does. */}
          <ThemeLogo mark className="size-7 group-hover:hidden group-focus-visible:hidden" />
          <PanelLeftOpen aria-hidden className="hidden group-hover:block group-focus-visible:block" />
        </button>
      </div>
      <div className="flex w-full shrink-0 flex-col items-center gap-1 border-b-[0.5px] border-foreground/20 py-2">
        <NavLink to="/" end className={railLink} aria-label={t("nav.home")} title={t("nav.home")}>
          <Home aria-hidden />
        </NavLink>
        <BellButton onOpen={onNotifications} className={railLink({ isActive: false })} />
        <button
          type="button"
          onClick={onSearch}
          aria-keyshortcuts="Meta+K Control+K"
          aria-label={t("search.open")}
          title={`${t("search.open")} (⌘K)`}
          className={railLink({ isActive: false })}
        >
          <Search aria-hidden />
        </button>
      </div>
      <nav aria-label={t("nav.label")} className="flex min-h-0 flex-1 flex-col items-center gap-1 overflow-y-auto py-2">
        {railItems(visibleSections(sections, expert.data)).map((item) => (
          <NavLink key={item.to} to={item.to} end className={railLink} aria-label={t(item.label)} title={t(item.label)}>
            <item.icon aria-hidden />
          </NavLink>
        ))}
      </nav>
      <Footer compact />
    </div>
  );
}

/** The selected theme's logo (ADR 0044): its wordmark for the mode shown, or its square mark; nothing without one. */
/** The selected theme's branding, as `Branding` draws it. */
function ThemeLogo({ mark = false, className }: { mark?: boolean; className?: string }) {
  return <Branding theme={useSelectedTheme()} dark={useDark()} mark={mark} className={className} />;
}

/** Short role labels, as in a chat app's account line; unknown roles show as they are. */
const ROLE_SHORT: Record<string, string> = {
  engineer: "IC",
  principal: "Principal",
  "engineering-manager": "EM",
  "engineering-leadership": "Eng Lead",
  "product-manager": "PM",
  "product-leadership": "Product Lead",
  "program-manager": "TPM",
};

/** The account at the bottom: the line with name and role, or in the rail just the avatar; both open the same menu. */
function Footer({ compact = false }: { compact?: boolean }) {
  const profile = useProfile();
  const me = profile.data;
  const shown = me?.name?.split(" ")[0] ?? me?.handle;
  const initial = (me?.name ?? me?.handle ?? "?").slice(0, 1).toUpperCase();
  const role = me?.role ? (ROLE_SHORT[me.role] ?? me.role.replaceAll("-", " ")) : undefined;
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const close = () => setOpen(false);
  return (
    <div
      ref={ref}
      className={cn(
        "relative flex items-center gap-1",
        // 60px like the status bar, so the hairlines above both line up across the window.
        "h-[60px] shrink-0 border-t-[0.5px] border-foreground/20",
        compact ? "w-full flex-col justify-center gap-0.5" : "-mx-2 mt-2 px-2",
      )}
    >
      {open && (
        <div
          role="menu"
          className={cn(
            "absolute z-30 rounded-xl border border-border bg-popover p-1.5 shadow-xl",
            compact ? "bottom-0 left-full ml-2 w-64" : "right-2 bottom-full left-2 mb-1.5",
          )}
        >
          {me?.email && <p className="truncate px-2.5 pt-1 pb-1.5 text-ui-sm text-muted-foreground">{me.email}</p>}
          <MenuLink to="?settings=" icon={Settings} onSelect={close} shortcut="⌘,">
            {t("nav.settings")}
          </MenuLink>
          <MenuLink to="/profile" icon={UserRound} onSelect={close}>
            {t("nav.profile")}
          </MenuLink>
          <MenuLink to="?onboarding=" icon={Sparkles} onSelect={close}>
            {t("account.setup")}
          </MenuLink>
          <MenuLink to="/doctor" icon={Stethoscope} onSelect={close}>
            {t("account.doctor")}
          </MenuLink>
          <ExpertItem />
          <MenuLink to="?panel=help" icon={CircleHelp} onSelect={close}>
            {t("account.help")}
          </MenuLink>
          <div className="px-1 py-0.5">
            <InstallApp compact />
          </div>
          <hr className="mx-2 my-1 border-border" />
          <MenuLink to="?panel=changelog" icon={FileClock} onSelect={close}>
            {t("account.changelog")}
          </MenuLink>
          <MenuLink href={`${REPO}#readme`} icon={Info} onSelect={close}>
            {t("account.learn")}
          </MenuLink>
        </div>
      )}
      {compact && <SoundButton compact />}
      {compact && <ThemeButton compact />}
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={compact ? (me?.name ?? t("nav.profile")) : undefined}
        title={[me?.name, me?.role?.replaceAll("-", " ")].filter(Boolean).join(" · ") || undefined}
        className={cn(
          "flex min-w-0 items-center gap-2 rounded-lg text-left transition-colors hover:bg-accent/70",
          compact ? "size-7 justify-center" : "flex-1 px-1.5 py-1.5",
        )}
      >
        <span
          aria-hidden
          className="flex size-6 shrink-0 items-center justify-center rounded-full bg-accent text-[11px] font-medium text-accent-foreground"
        >
          {initial}
        </span>
        {!compact && (
          <>
            <span className="truncate text-ui">{shown ?? t("nav.profile")}</span>
            {role && <span className="shrink-0 text-ui-sm text-muted-foreground">· {role}</span>}
            <ChevronDown aria-hidden className="size-3.5 shrink-0 text-icon" />
          </>
        )}
      </button>
      {!compact && <SoundButton />}
      {!compact && <ThemeButton />}
    </div>
  );
}

const REPO = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel";

type MenuLinkProps = {
  icon: LucideIcon;
  onSelect: () => void;
  shortcut?: string;
  children: React.ReactNode;
} & ({ to: string; href?: never } | { href: string; to?: never });

/** One row of the account menu: a screen of the app, or a page on GitHub in a new tab. */
function MenuLink({ icon: Icon, onSelect, shortcut, children, ...target }: MenuLinkProps) {
  const row =
    "flex w-full items-center gap-3 rounded-lg px-2.5 py-1.5 text-left text-ui text-foreground/90 transition-colors hover:bg-accent [&_svg]:size-(--icon) [&_svg]:shrink-0 [&_svg]:stroke-[1.75] [&_svg]:text-icon";
  const tail = shortcut ? (
    <kbd className="ml-auto font-sans text-xs text-muted-foreground">{shortcut}</kbd>
  ) : target.href ? (
    <ExternalLink aria-hidden className="ml-auto !size-3.5" />
  ) : null;
  return target.to === undefined ? (
    <a role="menuitem" href={target.href} target="_blank" rel="noreferrer" className={row} onClick={onSelect}>
      <Icon aria-hidden />
      {children}
      {tail}
    </a>
  ) : (
    <NavLink role="menuitem" to={target.to} className={row} onClick={onSelect}>
      <Icon aria-hidden />
      {children}
      {tail}
    </NavLink>
  );
}

/** Expert mode from the account menu: lists every screen and command when on (ADR 0041); takes effect at once. */
function ExpertItem() {
  const expert = useExpert();
  const change = useSetExpert();
  const on = expert.data?.expert ?? false;
  return (
    <button
      type="button"
      role="menuitemcheckbox"
      aria-checked={on}
      disabled={!expert.data || change.isPending}
      onClick={() => change.mutate(!on)}
      className="flex w-full items-center gap-3 rounded-lg px-2.5 py-1.5 text-left text-ui text-foreground/90 transition-colors hover:bg-accent disabled:opacity-50 [&_svg]:size-(--icon) [&_svg]:shrink-0 [&_svg]:stroke-[1.75] [&_svg]:text-icon"
    >
      <GraduationCap aria-hidden />
      {t("settings.expert")}
      <span
        aria-hidden
        className={cn("relative ml-auto h-4 w-7 rounded-full transition-colors", on ? "bg-switch" : "bg-accent")}
      >
        <span
          className={cn(
            "absolute top-0.5 left-0.5 size-3 rounded-full bg-white transition-transform",
            on && "translate-x-3",
          )}
        />
      </span>
    </button>
  );
}

function ThemeButton({ compact = false }: { compact?: boolean }) {
  const [theme, toggleTheme] = useTheme();
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggleTheme}
      aria-label={t("theme.toggle")}
      title={t("theme.toggle")}
      className={cn("text-icon", compact && "size-7")}
    >
      {theme === "dark" ? <Sun aria-hidden /> : <Moon aria-hidden />}
    </Button>
  );
}
