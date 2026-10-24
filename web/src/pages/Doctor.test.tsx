import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { doctor, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

describe("Doctor", () => {
  it("shows every check with its status and the fix as a command to copy", async () => {
    mockApi({ ...shell, "/api/system/doctor": doctor });
    const { container } = renderAt("/doctor");
    const row = (await screen.findByText("profile")).closest("tr") as HTMLElement;
    expect(within(row).getByText("degraded")).toBeInTheDocument();
    expect(within(row).getByText("hz setup")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Doctor" })).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("copies a fix command to the clipboard", async () => {
    mockApi({ ...shell, "/api/system/doctor": doctor });
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    renderAt("/doctor");
    await user.click(await screen.findByRole("button", { name: "Copy the command hz setup" }));
    expect(writeText).toHaveBeenCalledWith("hz setup");
  });

  it("reads a DOWN report from the 503 the API answers with", async () => {
    mockApi({ ...shell, "/api/system/doctor": { status: 503, body: { ...doctor, status: "down" } } });
    renderAt("/doctor");
    expect(await screen.findByText("down")).toBeInTheDocument();
  });

  it("sends the session token as a bearer header, never a cookie", async () => {
    const { calls } = mockApi({ ...shell, "/api/system/doctor": doctor });
    renderAt("/doctor");
    await screen.findByText("profile");
    const call = calls.find((request) => request.url.endsWith("/api/system/doctor"));
    expect(call?.headers.get("Authorization")).toBe("Bearer test-token");
    expect(call?.credentials).not.toBe("include");
  });

  it("shows the API's own words when a call fails", async () => {
    mockApi({ ...shell, "/api/system/doctor": { status: 400, body: { error: "Refused: something specific" } } });
    renderAt("/doctor");
    expect(await screen.findByRole("alert")).toHaveTextContent("Refused: something specific");
  });

  it("says to open the link again when the token is stale", async () => {
    mockApi({ ...shell, "/api/system/doctor": { status: 401, body: { error: "Refused: send the session token" } } });
    renderAt("/doctor");
    expect(await screen.findByRole("alert")).toHaveTextContent("Open the link the running `hz ui` printed");
  });
});
