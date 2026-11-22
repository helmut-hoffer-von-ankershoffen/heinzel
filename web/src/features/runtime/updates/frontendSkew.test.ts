import { beforeEach, expect, it, vi } from "vitest";
import { type UpdateTransport, updateTransport } from "./controller";
import { claimFrontendReload, frontendSkew } from "./frontendSkew";

const build = {
  version: "1.1.0",
  identity: "a".repeat(64),
  entrypoints: ["/assets/new.js"],
  files: { "/assets/new.js": "b".repeat(64) },
};
const signal = () => new AbortController().signal;
const transport = () =>
  ({ ...updateTransport, frontend: vi.fn(async () => build), assets: vi.fn(async () => {}) }) as UpdateTransport;

beforeEach(() => sessionStorage.clear());

it("verifies a mismatched build and allows one reload attempt per tab", async () => {
  const io = transport();
  expect(await frontendSkew("1.1.0", signal(), io, () => ["/assets/old.js"])).toBe(build.identity);
  expect(io.assets).toHaveBeenCalledWith(build, expect.any(AbortSignal), false);
  expect(claimFrontendReload(build.identity)).toBe(true);
  expect(await frontendSkew("1.1.0", signal(), io, () => ["/assets/old.js"])).toBeNull();
});

it("ignores matching, unknown, or mismatched server versions", async () => {
  const io = transport();
  expect(await frontendSkew("1.1.0", signal(), io, () => ["/assets/new.js"])).toBeNull();
  expect(await frontendSkew("1.1.0", signal(), io, () => ["/src/main.tsx"])).toBeNull();
  expect(await frontendSkew("1.0.0", signal(), io, () => ["/assets/old.js"])).toBeNull();
  expect(io.assets).not.toHaveBeenCalled();
});

it("does not mark a failed or aborted verification as a reload attempt", async () => {
  const io = transport();
  vi.mocked(io.assets).mockRejectedValueOnce(new Error("unavailable"));
  await expect(frontendSkew("1.1.0", signal(), io, () => ["/assets/old.js"])).rejects.toThrow("unavailable");
  expect(await frontendSkew("1.1.0", signal(), io, () => ["/assets/old.js"])).toBe(build.identity);
  const controller = new AbortController();
  vi.mocked(io.assets).mockImplementationOnce(async () => {
    controller.abort();
  });
  await expect(frontendSkew("1.1.0", controller.signal, io, () => ["/assets/old.js"])).rejects.toThrow();
  expect(sessionStorage.getItem("hz.frontend.skew-reload")).toBeNull();
});

it("does not allow a reload when session storage is unavailable", async () => {
  const storage = {
    getItem: () => null,
    setItem: () => {
      throw new Error("blocked");
    },
  } as unknown as Storage;
  expect(claimFrontendReload(build.identity, storage)).toBe(false);
});
