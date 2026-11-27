// The desktop app's menu and the footer ask the page to open things with one window event.
export const MENU_EVENT = "hz:menu";

export type MenuAction =
  | "about"
  | "settings"
  | "updates"
  | "changelog"
  | "doctor"
  | "copy"
  | "daemon"
  | "search"
  | "sidebar"
  | "terminal"
  | "browser"
  | "browser-show"
  | "chat"
  | "new-chat"
  | "new-tab"
  | "close-tab"
  | "notifications"
  | "help"
  | "go:today"
  | "go:heinzel"
  | "go:team"
  | "go:plugins";

/** Ask the page to do what a menu item or footer button names. */
export const requestMenuAction = (action: MenuAction) =>
  window.dispatchEvent(new CustomEvent(MENU_EVENT, { detail: action }));

/** Run `handle` for each matching menu event. A receiver can acknowledge a cancelable request with preventDefault. */
export function onMenuAction(
  actions: readonly MenuAction[],
  handle: (action: MenuAction, event: Event) => void,
): () => void {
  const listener = (event: Event) => {
    const action = (event as CustomEvent<MenuAction>).detail;
    if (actions.includes(action)) handle(action, event);
  };
  window.addEventListener(MENU_EVENT, listener);
  return () => window.removeEventListener(MENU_EVENT, listener);
}
