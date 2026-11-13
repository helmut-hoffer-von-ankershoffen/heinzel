// The plan and the answers of "Update all" as they go over the wire: a preview never installs, and an agreed update
// says so (`yes`) with the person's choice for the desktop app.
import { beforeEach, expect, it, vi } from "vitest";
import { stream } from "@/api/stream";
import { pendingUpdate, readUpdatePlan, startUpdate, type UpdateTransport, updateTransport } from "./controller";

vi.mock("@/api/stream", () => ({ stream: vi.fn() }));

const plan = { current: "0.1.0", latest: "0.2.0", core: "hz 0.1.0 → 0.2.0.", desktop: true, plugins: [], token: "t1" };
const signal = () => new AbortController().signal;

beforeEach(() => {
  vi.mocked(stream).mockReset();
  sessionStorage.clear();
});

it("reads the plan with a preview call, which installs nothing", async () => {
  vi.mocked(stream).mockResolvedValue({ previewed: true, updated: false, plan });
  expect(await readUpdatePlan(signal())).toEqual(plan);
  expect(vi.mocked(stream).mock.calls[0]?.slice(0, 2)).toEqual(["/system/update", { preview: true }]);
});

it("refuses an answer that is no preview or names no plan, so nobody agrees to something unseen", async () => {
  vi.mocked(stream).mockResolvedValue({ previewed: false, updated: true, plan });
  await expect(readUpdatePlan(signal())).rejects.toThrow("The server sent no update plan.");
  vi.mocked(stream).mockResolvedValue({ previewed: true, updated: false, plan: { ...plan, token: "" } });
  await expect(readUpdatePlan(signal())).rejects.toThrow("The server sent no update plan.");
  vi.mocked(stream).mockResolvedValue({ previewed: true, updated: false, plan: null });
  await expect(readUpdatePlan(signal())).rejects.toThrow("The server sent no update plan.");
});

it("sends the agreed update with yes, the token of the shown plan and the desktop choice", async () => {
  vi.mocked(stream).mockResolvedValue({});
  await updateTransport.install(signal(), undefined, { plan: "t1", desktop: true });
  await updateTransport.install(signal(), undefined, { plan: "t1", desktop: false });
  await updateTransport.install(signal(), undefined, { plan: "t1" });
  expect(vi.mocked(stream).mock.calls.map((call) => call[1])).toEqual([
    { yes: true, plan: "t1", desktop: true },
    { yes: true, plan: "t1", desktop: false },
    { yes: true, plan: "t1", desktop: false },
  ]);
});

it("sends an update nobody was asked about without answers, so pins and the desktop app stay", async () => {
  vi.mocked(stream).mockResolvedValue({});
  await updateTransport.install(signal());
  expect(vi.mocked(stream).mock.calls[0]?.[1]).toEqual({});
});

it("hands the answers of the confirmation to the installer", async () => {
  const install = vi.fn(async (_signal: AbortSignal, _onPhase?: unknown, _answers?: unknown) => ({
    before: "0.1.0",
    after: "0.1.0",
    latest: null,
    pinned_to: null,
    updated: false,
    message: "hz 0.1.0 is up to date.",
    next: [],
    plugins: [],
    previewed: false,
  }));
  const transport = {
    lifecycle: { read: vi.fn(async () => ({ restartable: true })) },
    install,
  } as unknown as UpdateTransport;
  const answers = { plan: "t1", desktop: true };
  expect(await startUpdate(signal(), vi.fn(), transport, vi.fn(), answers)).toBe("hz 0.1.0 is up to date.");
  expect(install.mock.calls[0]?.[2]).toBe(answers);
});

it("fails with the server's words and keeps no receipt when the plan changed and nothing was installed", async () => {
  const install = vi.fn(async (_signal: AbortSignal, _onPhase?: unknown, _answers?: unknown) => ({
    before: "0.1.0",
    after: "0.1.0",
    latest: "0.2.0",
    pinned_to: null,
    updated: false,
    message: "The plan changed since it was shown, so nothing was changed. This is the plan now.",
    next: ["hz system update --yes"],
    plugins: [],
    previewed: true,
    plan: { ...plan, token: "t2" },
  }));
  const transport = {
    lifecycle: { read: vi.fn(async () => ({ restartable: true })) },
    install,
  } as unknown as UpdateTransport;
  await expect(startUpdate(signal(), vi.fn(), transport, vi.fn(), { plan: "t1" })).rejects.toThrow(
    "The plan changed since it was shown",
  );
  expect(pendingUpdate()).toBeNull();
});
