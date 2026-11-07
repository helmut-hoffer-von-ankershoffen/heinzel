import type * as React from "react";
import type * as ReactDOM from "react-dom";
import type * as Router from "react-router";

/** Host data is the validated result of the declaration's read-only source operation. */
export interface ViewProps<T = unknown> {
  data: T | undefined;
  loading: boolean;
  visible: boolean;
  compact: boolean;
  flowing: boolean;
  onClose(): void;
  /** Runs only an action declared by this view, through the host's ordinary confirmation gate. */
  run(name: string): void;
}
export interface PluginSDK {
  abi: 1;
  React: typeof React;
  ReactDOM: typeof ReactDOM;
  router: typeof Router;
  Button: React.ComponentType<React.ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: "default" | "secondary" | "ghost" | "outline" | "destructive" | "link";
    size?: "default" | "sm" | "lg" | "icon" | "icon-sm";
  }>;
  SettingsShortcut: React.ComponentType<{
    section: string; label: string; onClick?: () => void; className?: string;
  }>;
  /** The one action row under a divider: the first action shows its words, the rest are icons (design: "Actions"). */
  PaneActions: React.ComponentType<{
    children?: React.ReactNode; className?: string; align?: "start" | "end"; words?: "lead" | "all";
  }>;
  /** One action: `icon` is required, `children` are its words (shown on the lead, else its name and tooltip). */
  PaneAction: React.ComponentType<Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
    icon: React.ComponentType<{ "aria-hidden"?: boolean | "true"; className?: string }>;
    children: string; primary?: boolean; destructive?: boolean; ghost?: boolean; to?: string; href?: string;
  }>;
  /** A title row with at most three ghost `PaneAction`s acting on the container itself. */
  PaneTitle: React.ComponentType<{ children: React.ReactNode; chrome?: React.ReactNode }>;
}
export type ViewFactory<T = unknown> = (sdk: PluginSDK) => React.ComponentType<ViewProps<T>>;
