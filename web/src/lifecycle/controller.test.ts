import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import {
  clearOwnedShellCaches,
  type LifecycleTransport,
  lifecycleTransport,
  RestartApplyError,
  restartAndApply,
  resumeRestartAndApply,
} from "./controller";

const before = { instance: "old", version: "0.7.7", restartable: true };
const after = { instance: "new", version: "0.7.8", restartable: true };
const setting = { section: "clock", key: "zones", value: ["Europe/Berlin"] };

function server() {
  return {
    read: vi.fn<LifecycleTransport["read"]>().mockResolvedValue(after).mockResolvedValueOnce(before),
    restart: vi.fn<LifecycleTransport["restart"]>().mockResolvedValue({ restarting: true, instance: before.instance }),
    verify: vi
      .fn<LifecycleTransport["verify"]>()
      .mockImplementation(async (expected) => ({ ...expected, matches: true, mismatched: [] })),
  };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("verified restart and apply", () => {
  it("retries a preflight disconnect before one confirmed mutation", async () => {
    const transport = server();
    let first: AbortSignal | undefined;
    transport.read
      .mockReset()
      .mockImplementationOnce((signal) => {
        first = signal;
        return new Promise(() => {});
      })
      .mockResolvedValue(before);
    const preserve = vi.fn(async () => false);
    const result = restartAndApply(
      { confirmed: true, preserve },
      { transport, requestTimeout: 10, readinessTimeout: 30, interval: 1, preserveTimeout: 20 * 60_000 },
    );
    await vi.advanceTimersByTimeAsync(12);
    await expect(result).resolves.toEqual(before);
    expect(first?.aborted).toBe(true);
    expect(transport.read).toHaveBeenCalledTimes(2);
    expect(preserve).toHaveBeenCalledOnce();
    expect(transport.restart).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("gives confirmed long-running work its own budget and keeps restart observation bounded", async () => {
    const transport = server();
    transport.read.mockResolvedValue(before);
    const preserve = vi.fn(() => new Promise<boolean>((resolve) => setTimeout(() => resolve(true), 18)));
    transport.restart.mockImplementation(() => new Promise(() => {}));
    const result = restartAndApply(
      { confirmed: true, version: after.version, preserve },
      { transport, requestTimeout: 10, preserveTimeout: 25, timeout: 20, interval: 1 },
    ).catch((error: unknown) => error);
    await vi.runAllTimersAsync();
    await expect(result).resolves.toMatchObject({ kind: "timeout", observation: { retired: [before.instance] } });
    expect(preserve).toHaveBeenCalledOnce();
    expect(transport.restart).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("requires explicit confirmation before any reads or mutation", async () => {
    const transport = server();
    await expect(restartAndApply({ confirmed: false }, { transport })).rejects.toMatchObject({ kind: "confirmation" });
    expect(transport.read).not.toHaveBeenCalled();
    expect(transport.restart).not.toHaveBeenCalled();
  });

  it("preserves work before one restart and verifies the exact new instance, version and selected target", async () => {
    const transport = server();
    const preserve = vi.fn(async () => {
      expect(transport.restart).not.toHaveBeenCalled();
      return undefined;
    });
    const phases: string[] = [];
    const result = await restartAndApply(
      { confirmed: true, version: after.version, settings: [setting], preserve },
      { transport, onPhase: (phase) => phases.push(phase) },
    );
    expect(result).toEqual(after);
    expect(preserve).toHaveBeenCalledOnce();
    expect(transport.restart).toHaveBeenCalledOnce();
    expect(transport.verify).toHaveBeenCalledWith(
      { instance: after.instance, version: after.version, settings: [setting] },
      expect.any(AbortSignal),
    );
    expect(phases).toEqual(["checking", "preserving", "restarting", "reconnecting", "verifying", "applied"]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not restart after a confirmed apply reports that a restart is unnecessary", async () => {
    const transport = server();
    await expect(restartAndApply({ confirmed: true, preserve: async () => false }, { transport })).resolves.toEqual(
      before,
    );
    expect(transport.restart).not.toHaveBeenCalled();
    expect(transport.verify).not.toHaveBeenCalled();
  });

  it("never accepts repeated successful answers from the retiring process", async () => {
    const transport = server();
    transport.read.mockResolvedValue(before);
    const result = restartAndApply({ confirmed: true }, { transport, timeout: 50, interval: 10 });
    const failed = expect(result).rejects.toMatchObject({
      kind: "timeout",
      observation: { retired: [before.instance] },
    });
    await vi.runAllTimersAsync();
    await failed;
    expect(transport.verify).not.toHaveBeenCalled();
    expect(transport.restart).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("waits through old-process 200 responses and a disconnect before accepting the replacement", async () => {
    const transport = server();
    transport.read.mockResolvedValueOnce(before).mockRejectedValueOnce(new TypeError("Disconnected"));
    const result = restartAndApply({ confirmed: true, version: after.version }, { transport, interval: 10 });
    await vi.runAllTimersAsync();
    await expect(result).resolves.toEqual(after);
    expect(transport.read).toHaveBeenCalledTimes(4);
    expect(transport.restart).toHaveBeenCalledOnce();
  });

  it("retries a fetch aborted by the retiring server while observing its replacement", async () => {
    const transport = server();
    transport.read
      .mockResolvedValueOnce(before)
      .mockRejectedValueOnce(new DOMException("The operation was aborted", "AbortError"));
    const result = restartAndApply({ confirmed: true, version: after.version }, { transport, interval: 10 });
    await vi.runAllTimersAsync();
    await expect(result).resolves.toEqual(after);
    expect(transport.read).toHaveBeenCalledTimes(4);
    expect(transport.verify).toHaveBeenCalledOnce();
    expect(transport.restart).toHaveBeenCalledOnce();
  });

  it("observes after a dropped acknowledgement without replaying a mutation", async () => {
    const transport = server();
    transport.restart.mockRejectedValue(new TypeError("Connection closed"));
    await expect(restartAndApply({ confirmed: true, version: after.version }, { transport })).resolves.toEqual(after);
    expect(transport.restart).toHaveBeenCalledOnce();
  });

  it("also waits past an acknowledged instance that changed during preflight", async () => {
    const transport = server();
    transport.restart.mockResolvedValue({ restarting: true, instance: "intermediate" });
    transport.read.mockResolvedValueOnce({ ...after, instance: "intermediate" });
    const result = restartAndApply({ confirmed: true, version: after.version }, { transport, interval: 10 });
    await vi.runAllTimersAsync();
    await expect(result).resolves.toEqual(after);
    expect(transport.verify).toHaveBeenCalledOnce();
  });

  it.each([
    ["version", false],
    ["configuration", true],
  ] as const)("does not apply a replacement with the wrong %s", async (kind, correctVersion) => {
    const transport = server();
    transport.read.mockResolvedValue({ ...after, version: correctVersion ? after.version : before.version });
    transport.verify.mockImplementation(async (expected) => ({
      ...expected,
      matches: false,
      mismatched: ["clock.zones"],
    }));
    const onPhase = vi.fn();
    const result = restartAndApply(
      { confirmed: true, version: after.version, settings: [setting] },
      { transport, onPhase, timeout: 50, interval: 10 },
    );
    const failed = expect(result).rejects.toMatchObject({ kind });
    await vi.runAllTimersAsync();
    await failed;
    expect(onPhase).not.toHaveBeenCalledWith("applied");
  });

  it("binds the verification answer to the same instance and version", async () => {
    const transport = server();
    transport.verify.mockImplementation(async (expected) => ({
      ...expected,
      instance: "unrelated",
      matches: true,
      mismatched: [],
    }));
    const result = restartAndApply(
      { confirmed: true, version: after.version },
      { transport, timeout: 50, interval: 10 },
    );
    const failed = expect(result).rejects.toMatchObject({ kind: "configuration" });
    await vi.runAllTimersAsync();
    await failed;
  });

  it("reports unsupported restart with an observation the caller can check after a manual restart", async () => {
    const transport = server();
    transport.read.mockReset().mockResolvedValue({ ...before, restartable: false });
    await expect(restartAndApply({ confirmed: true }, { transport })).rejects.toMatchObject({
      kind: "unsupported",
      observation: { retired: [before.instance] },
    });
    expect(transport.restart).not.toHaveBeenCalled();
  });

  it("fails before mutation when preserving open work fails", async () => {
    const transport = server();
    await expect(
      restartAndApply(
        {
          confirmed: true,
          preserve: async () => {
            throw new Error("Draft could not be saved");
          },
        },
        { transport },
      ),
    ).rejects.toThrow("Draft could not be saved");
    expect(transport.restart).not.toHaveBeenCalled();
  });

  it("does not turn an explicit refusal into a successful reconnect", async () => {
    const transport = server();
    transport.restart.mockRejectedValue(new ApiError(400, "Restart refused"));
    await expect(restartAndApply({ confirmed: true }, { transport })).rejects.toMatchObject({ kind: "refused" });
    expect(transport.read).toHaveBeenCalledOnce();
  });

  it("ends verification immediately when authentication is lost", async () => {
    const transport = server();
    transport.read.mockRejectedValueOnce(new ApiError(401, "Pair this device again"));
    await expect(restartAndApply({ confirmed: true }, { transport })).rejects.toThrow("Pair this device again");
    expect(transport.restart).toHaveBeenCalledOnce();
  });

  it("bounds a request that never answers and retains observation after an uncertain POST", async () => {
    const transport = server();
    transport.restart.mockImplementation(() => new Promise(() => {}));
    const result = restartAndApply(
      { confirmed: true, version: after.version },
      { transport, requestTimeout: 10, timeout: 100 },
    );
    await vi.runAllTimersAsync();
    await expect(result).resolves.toEqual(after);
    expect(transport.restart).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("cancels an in-flight restart check without pretending the mutation was cancelled", async () => {
    const transport = server();
    const controller = new AbortController();
    transport.restart.mockImplementation(async () => {
      controller.abort();
      return new Promise(() => {});
    });
    await expect(restartAndApply({ confirmed: true }, { transport, signal: controller.signal })).rejects.toMatchObject({
      kind: "aborted",
      observation: { retired: [before.instance] },
    });
    expect(vi.getTimerCount()).toBe(0);
  });

  it("can resume after a timeout without sending another restart POST", async () => {
    const transport = server();
    transport.read.mockResolvedValue(before);
    const result = restartAndApply(
      { confirmed: true, version: after.version },
      { transport, timeout: 50, interval: 10 },
    ).catch((error: RestartApplyError) => error);
    await vi.runAllTimersAsync();
    const failure = await result;
    expect(failure).toBeInstanceOf(RestartApplyError);
    if (!(failure instanceof RestartApplyError) || !failure.observation)
      throw new Error("Missing retained observation");
    transport.read.mockResolvedValue(after);
    await expect(resumeRestartAndApply(failure.observation, { transport })).resolves.toEqual(after);
    expect(transport.restart).toHaveBeenCalledOnce();
  });

  it("clears only owned shell caches", async () => {
    const storage = {
      keys: vi.fn().mockResolvedValue(["hz-shell-v1", "hz-shell-v2", "other-app", "hz-unrelated"]),
      delete: vi.fn().mockResolvedValue(true),
    };
    await clearOwnedShellCaches(storage);
    expect(storage.delete.mock.calls).toEqual([["hz-shell-v1"], ["hz-shell-v2"]]);
  });

  it("cannot resume without a retiring server identity", async () => {
    const transport = server();
    await expect(
      resumeRestartAndApply({ retired: [], version: after.version, settings: [] }, { transport }),
    ).rejects.toMatchObject({ kind: "configuration" });
    expect(transport.read).not.toHaveBeenCalled();
    expect(transport.restart).not.toHaveBeenCalled();
  });

  it("uses the existing bearer client, same origin and no-store for every lifecycle request", async () => {
    sessionStorage.setItem("hz.token", "lifecycle-test");
    const requests: Request[] = [];
    const expected = { instance: after.instance, version: after.version, settings: [setting] };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        requests.push(request);
        const path = new URL(request.url).pathname;
        const body = path.endsWith("/verify")
          ? { ...expected, matches: true, mismatched: [] }
          : path.endsWith("/restart")
            ? { restarting: true, instance: before.instance }
            : before;
        return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
      }),
    );
    const controller = new AbortController();
    await lifecycleTransport.read(controller.signal);
    await lifecycleTransport.restart(controller.signal);
    await lifecycleTransport.verify(expected, controller.signal);
    expect(requests.map((request) => [new URL(request.url).pathname, request.method])).toEqual([
      ["/api/lifecycle", "GET"],
      ["/api/restart", "POST"],
      ["/api/lifecycle/verify", "POST"],
    ]);
    for (const request of requests) {
      expect(new URL(request.url).origin).toBe(window.location.origin);
      expect(request.headers.get("Authorization")).toBe("Bearer lifecycle-test");
      expect(request.cache).toBe("no-store");
    }
    expect(await requests[2]?.json()).toEqual(expected);
  });
});
