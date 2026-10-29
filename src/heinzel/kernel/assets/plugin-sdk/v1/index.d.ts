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
}
export type ViewFactory<T = unknown> = (sdk: PluginSDK) => React.ComponentType<ViewProps<T>>;
