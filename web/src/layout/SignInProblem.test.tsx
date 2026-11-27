import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { SignInProblem, signInStep } from "./SignInProblem";

const run = vi.hoisted(() => vi.fn());
vi.mock("@/components/CommandActions", () => ({ runCommand: run }));

const expired =
  "gws is not signed in to Google, or its sign-in expired (invalid_grant). Sign in again: hz setup google.";

it("names the setup step an expired sign-in points to", () => {
  expect(signInStep(expired)).toEqual({ command: "hz setup google", name: "Google" });
  expect(signInStep("No calendar connected.")).toBeNull();
});

it("offers a Sign in to Google button and folds the raw error into details", async () => {
  render(<SignInProblem problem={expired} />);
  await userEvent.click(screen.getByRole("button", { name: "Sign in to Google" }));
  expect(run).toHaveBeenCalledWith("hz setup google");
  expect(screen.getByText("Details")).toBeInTheDocument();
});

it("shows any other problem as it is", () => {
  render(<SignInProblem problem="No calendar connected." />);
  expect(screen.getByText("No calendar connected.")).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
