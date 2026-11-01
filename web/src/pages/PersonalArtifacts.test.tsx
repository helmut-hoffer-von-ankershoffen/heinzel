import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { expectAccessible, mockApi } from "@/test/render";
import { PersonalArtifacts } from "./PersonalArtifacts";

const first = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const second = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW";
const sessions = {
  sessions: [
    { session_id: first, name: "Atlas" },
    { session_id: second, name: "Briar" },
  ],
};
const item = (kind: string, path: string) => ({
  session_id: first,
  request_id: kind,
  provider: "google",
  kind,
  ref: `https://docs.google.com/${path}/d/abc123_DEF456/edit`,
  provenance: "principal-submitted",
});
afterEach(() => sessionStorage.clear());

describe("local personal artifact associations", () => {
  it("lists only the selected session's validated links without contacting the provider", async () => {
    sessionStorage.setItem("hz.token", "test-token");
    const { calls } = mockApi({
      "/api/heinzel/sessions": sessions,
      "/api/heinzel/personal-artifacts": {
        session_id: first,
        artifacts: [
          item("document", "document"),
          item("spreadsheet", "spreadsheets"),
          item("presentation", "presentation"),
        ],
      },
    });
    const user = userEvent.setup();
    const { container } = render(<PersonalArtifacts />);
    const select = await screen.findByRole("combobox", { name: "Choose a personal helper" });
    expect(calls.some((request) => request.url.includes("personal-artifacts"))).toBe(false);
    await user.selectOptions(select, first);
    expect(await screen.findByRole("link", { name: /Google Doc/ })).toHaveAttribute(
      "href",
      item("document", "document").ref,
    );
    expect(screen.getByRole("link", { name: /Google Sheet/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Google Slides/ })).toBeInTheDocument();
    expect(screen.getAllByText(/You added these links/)).toHaveLength(4);
    const list = calls.find((request) => request.url.includes("personal-artifacts"));
    expect(new URL(list?.url ?? "").searchParams.get("session_id")).toBe(first);
    expect(list?.headers.get("Authorization")).toBe("Bearer test-token");
    expect(calls.every((request) => new URL(request.url).origin === window.location.origin)).toBe(true);
    await expectAccessible(container);
  });

  it.each([
    { ...item("document", "document"), ref: "https://docs.google.com.evil.example/document/d/abc123_DEF456/edit" },
    { ...item("document", "document"), ref: "javascript:alert(1)" },
    { ...item("document", "document"), ref: `${item("document", "document").ref}?token=secret` },
    { ...item("document", "document"), ref: `${item("document", "document").ref}\n` },
    { ...item("document", "document"), session_id: second },
    { ...item("document", "document"), provenance: "verified" },
  ])("rejects an invalid or incorrectly scoped association", async (bad) => {
    mockApi({
      "/api/heinzel/sessions": sessions,
      "/api/heinzel/personal-artifacts": { session_id: first, artifacts: [bad] },
    });
    const user = userEvent.setup();
    render(<PersonalArtifacts />);
    await user.selectOptions(await screen.findByRole("combobox", { name: "Choose a personal helper" }), first);
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be loaded or validated");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("does not show an old session's result after switching", async () => {
    let finish: ((value: unknown) => void) | undefined;
    mockApi({
      "/api/heinzel/sessions": sessions,
      "/api/heinzel/personal-artifacts": (request: Request) =>
        new URL(request.url).searchParams.get("session_id") === first
          ? new Promise((resolve) => {
              finish = resolve;
            })
          : { session_id: second, artifacts: [] },
    });
    const user = userEvent.setup();
    render(<PersonalArtifacts />);
    const select = await screen.findByRole("combobox", { name: "Choose a personal helper" });
    await user.selectOptions(select, first);
    await waitFor(() => expect(finish).toBeDefined());
    await user.selectOptions(select, second);
    expect(await screen.findByText("No local file associations for this helper.")).toBeInTheDocument();
    finish?.({ session_id: first, artifacts: [item("document", "document")] });
    await waitFor(() => expect(screen.queryByRole("link")).not.toBeInTheDocument());
  });
});
