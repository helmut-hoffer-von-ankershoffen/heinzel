import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, it } from "vitest";
import { WorkspacePanels } from "./WorkspacePanels";

function Example() {
  const [active, select] = useState("chat");
  return (
    <div className="flex">
      <WorkspacePanels
        active={active}
        onSelect={select}
        panels={[
          { id: "chat", title: "Chat", content: <input aria-label="Draft" defaultValue="" /> },
          { id: "terminal", title: "Terminal", content: <input aria-label="Command" defaultValue="" /> },
          { id: "clock", title: "World clock", content: <p>Local clocks</p> },
          { id: "plugin", title: "Plugin panel", content: <p>Contributed content</p> },
        ]}
      />
    </div>
  );
}

it("gives one panel the space while preserving drafts and mounted state across built-in and contributed tabs", async () => {
  const user = userEvent.setup();
  render(<Example />);
  const draft = screen.getByRole("textbox", { name: "Draft" });
  await user.type(draft, "Keep this draft");
  await user.click(screen.getByRole("tab", { name: "Terminal" }));
  const command = screen.getByRole("textbox", { name: "Command" });
  await user.type(command, "pending command");
  await user.click(screen.getByRole("tab", { name: "World clock" }));
  expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
  expect(draft).not.toBeVisible();
  expect(command).not.toBeVisible();
  await user.click(screen.getByRole("tab", { name: "Plugin panel" }));
  expect(screen.getByText("Contributed content")).toBeVisible();
  await user.click(screen.getByRole("tab", { name: "Chat" }));
  expect(screen.getByRole("textbox", { name: "Draft" })).toBe(draft);
  expect(draft).toHaveValue("Keep this draft");
  await user.click(screen.getByRole("tab", { name: "Terminal" }));
  expect(screen.getByRole("textbox", { name: "Command" })).toBe(command);
  expect(command).toHaveValue("pending command");
});

it("supports arrow, Home and End selection with one keyboard tab stop", async () => {
  const user = userEvent.setup();
  render(<Example />);
  screen.getByRole("tab", { name: "Chat" }).focus();
  await user.keyboard("{End}");
  expect(screen.getByRole("tab", { name: "Plugin panel", selected: true })).toHaveFocus();
  await user.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Chat", selected: true })).toHaveFocus();
  await user.keyboard("{ArrowLeft}");
  expect(screen.getByRole("tab", { name: "Plugin panel", selected: true })).toHaveFocus();
  await user.keyboard("{Home}");
  expect(screen.getByRole("tab", { name: "Chat", selected: true })).toHaveFocus();
  expect(screen.getAllByRole("tab").filter((tab) => tab.tabIndex === 0)).toHaveLength(1);
});

it("selects a surviving descriptor when the active contributed panel is removed", () => {
  const panels = [
    { id: "chat", title: "Chat", content: <p>Conversation</p> },
    { id: "clock", title: "World clock", content: <p>Clocks</p> },
    { id: "plugin", title: "Plugin panel", content: <p>Plugin content</p> },
  ];
  const { rerender } = render(<WorkspacePanels panels={panels} active="plugin" onSelect={() => {}} />);
  rerender(<WorkspacePanels panels={panels.slice(0, 2)} active="plugin" onSelect={() => {}} />);
  expect(screen.getByRole("tab", { name: "Chat", selected: true })).toBeInTheDocument();
  expect(screen.getByRole("tabpanel", { name: "Chat" })).toBeVisible();
  expect(screen.queryByText("Plugin content")).not.toBeInTheDocument();
  expect(screen.getAllByRole("tab").filter((tab) => tab.tabIndex === 0)).toHaveLength(1);
});
