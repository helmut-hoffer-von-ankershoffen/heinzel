// Three panes at full height: navigation on the left, the screen in the middle, the chat on the right (ADR 0039).
import { MessageSquare, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useEffect, useState } from "react";
import { Outlet } from "react-router";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { ChatPanel } from "./ChatPanel";
import { SearchPalette } from "./SearchPalette";
import { Sidebar } from "./Sidebar";

export function Shell() {
  const [navOpen, setNavOpen] = useState(true);
  const [chatOpen, setChatOpen] = useState(true);
  const [searchOpen, setSearchOpen] = useState(false);
  useEffect(() => {
    const open = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", open);
    return () => window.removeEventListener("keydown", open);
  }, []);
  return (
    <div className="flex h-full">
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-card focus:p-2"
      >
        {t("app.skip")}
      </a>
      <aside
        aria-label={t("nav.label")}
        className={cn("shrink-0 border-r border-border bg-muted/50", navOpen ? "w-64" : "hidden")}
      >
        <Sidebar onSearch={() => setSearchOpen(true)} />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-border px-3 py-2">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setNavOpen((open) => !open)}
            aria-expanded={navOpen}
            aria-label={navOpen ? t("nav.collapse") : t("nav.expand")}
            title={navOpen ? t("nav.collapse") : t("nav.expand")}
          >
            {navOpen ? <PanelLeftClose aria-hidden /> : <PanelLeftOpen aria-hidden />}
          </Button>
          <span className="font-semibold">
            <span aria-hidden>🧝 </span>
            {t("app.name")}
          </span>
          {!chatOpen && (
            <Button
              variant="outline"
              size="sm"
              className="ml-auto"
              onClick={() => setChatOpen(true)}
              aria-expanded={false}
            >
              <MessageSquare aria-hidden />
              {t("chat.expand")}
            </Button>
          )}
        </header>
        <main id="content" tabIndex={-1} className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
      {chatOpen && (
        <aside aria-label={t("chat.title")} className="w-80 shrink-0 border-l border-border bg-card max-md:hidden">
          <ChatPanel onCollapse={() => setChatOpen(false)} />
        </aside>
      )}
      <SearchPalette open={searchOpen} onOpenChange={setSearchOpen} />
    </div>
  );
}
