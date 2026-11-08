import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { expectAccessible } from "@/test/render";
import { Progress, type ProgressSegment } from "./Progress";

const states: ProgressSegment[] = [
  { id: "a", label: "Heinzel", state: "done" },
  { id: "b", label: "notes", state: "running" },
  { id: "c", label: "mail", state: "pending" },
  { id: "d", label: "git", state: "failed" },
  { id: "e", label: "docs", state: "warn" },
  { id: "f", label: "old", state: "skipped" },
];

it("names every segment and its state, counts the finished ones, and passes axe", async () => {
  const { container } = render(<Progress label="Update progress" segments={states} />);
  const bar = screen.getByRole("progressbar", { name: "Update progress" });
  expect(bar).toHaveAttribute("max", "6");
  expect(bar).toHaveAttribute("value", "3");
  expect(bar).toHaveAttribute("aria-valuetext", "3 of 6");
  for (const [name, state] of [
    ["Heinzel", "done"],
    ["notes", "in progress"],
    ["mail", "waiting"],
    ["git", "failed"],
    ["docs", "needs a look"],
    ["old", "skipped"],
  ])
    expect(screen.getByText(`${name}: ${state}`)).toBeInTheDocument();
  await expectAccessible(container);
});

it("pulses only the running segment, and only when asked and motion is allowed", () => {
  const { container, rerender } = render(<Progress label="Steps" segments={states} />);
  expect(container.querySelectorAll(".motion-safe\\:animate-pulse")).toHaveLength(1);
  rerender(<Progress label="Steps" segments={states} pulse={false} />);
  expect(container.querySelector(".motion-safe\\:animate-pulse")).toBeNull();
});

it("draws a plain count as one bar with the same role and no names list", async () => {
  const { container } = render(<Progress label="Imported" value={3} max={8} />);
  const bar = screen.getByRole("progressbar", { name: "Imported" });
  expect(bar).toHaveAttribute("aria-valuetext", "3 of 8");
  expect(screen.queryByRole("list")).not.toBeInTheDocument();
  await expectAccessible(container);
});

it("keeps a custom spoken value and an empty list draws nothing", () => {
  const { container, rerender } = render(
    <Progress label="Steps" segments={states} names={false} valueText="Step 2 of 6" />,
  );
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuetext", "Step 2 of 6");
  expect(screen.queryByRole("list")).not.toBeInTheDocument();
  rerender(<Progress label="Steps" segments={[]} />);
  expect(container).toBeEmptyDOMElement();
});
