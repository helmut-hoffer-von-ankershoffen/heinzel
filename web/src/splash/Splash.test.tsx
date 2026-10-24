import { readFileSync } from "node:fs";
import { join } from "node:path";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { acmeTheme, themeList } from "@/test/fixtures";
import { expectAccessible, mockApi } from "@/test/render";
import { GIVE_UP_MS, initial, reduce } from "./machine";
import { type ProbeResult, Splash, SplashGone } from "./Splash";
import { createSplashStore } from "./store";

const page = readFileSync(join(process.cwd(), "index.html"), "utf8"); // vitest runs in web/
const markup = /<div id="splash"[\s\S]*?<\/div>\s*(?=<script>)/.exec(page)?.[0] ?? "";
const ok: ProbeResult = { kind: "ok", info: { version: "0.9.1" } };

let started = 0;
const now = () => Date.now() - started;
const splash = () => document.getElementById("splash") as HTMLElement;
const line = () => document.getElementById("splash-line")?.textContent;

function mount(probe: () => Promise<ProbeResult>, store = createSplashStore()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <Splash store={store} probe={probe} now={now} />
    </QueryClientProvider>,
    { container: document.getElementById("root") as HTMLElement },
  );
  return { store, client, view };
}

const passes = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

beforeEach(() => {
  vi.useFakeTimers();
  started = Date.now();
  document.body.innerHTML = `${markup}<div id="root"></div>`;
  mockApi({ "/api/system/theme/list": themeList("acme") });
});

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = "";
});

describe("the splash", () => {
  it("connects, signs in, then fades out once the theme is on the page, never before its least time", async () => {
    const probe = vi.fn(async () => ok);
    const { store } = mount(probe);
    await passes(0);
    expect(line()).toBe("Signing you in…");
    expect(document.getElementById("root")).toHaveAttribute("inert");
    act(() => store.send({ type: "themed" }));
    await passes(250);
    expect(splash()).not.toHaveAttribute("data-leaving"); // 300 ms at least
    await passes(100);
    expect(splash()).toHaveAttribute("data-leaving");
    expect(document.getElementById("root")).not.toHaveAttribute("inert");
    await passes(250);
    expect(splash().hidden).toBe(true);
    expect(probe).toHaveBeenCalledTimes(1);
    expect(document.getElementById("splash-version")?.textContent).toBe("v0.9.1");
    expect(document.querySelector("#splash-brand img")).toHaveAttribute("alt", acmeTheme.logo?.alt);
  });

  it("keeps asking while hz doesn't answer, shows the fix after the timeout, and connects when hz starts", async () => {
    let answer: ProbeResult = { kind: "unreachable" };
    const probe = vi.fn(async () => answer);
    const { store } = mount(probe);
    await passes(0);
    expect(line()).toBe("Connecting to hz…");
    await passes(GIVE_UP_MS - 100);
    expect(screen.queryByRole("alert")).toBeNull();
    await passes(200);
    expect(screen.getByRole("alert")).toHaveTextContent("hz isn't answering");
    expect(screen.getByRole("button", { name: "Copy the command hz ui" })).toBeInTheDocument();
    expect(splash().dataset.state).toBe("error");
    answer = ok;
    act(() => store.send({ type: "themed" }));
    await passes(1_000);
    expect(screen.queryByRole("alert")).toBeNull();
    await passes(500);
    expect(splash().hidden).toBe(true);
  });

  it("says the sign-in expired, with the command, when hz refuses the token", async () => {
    const probe = vi.fn(async (): Promise<ProbeResult> => ({ kind: "refused" }));
    mount(probe);
    await passes(0);
    expect(screen.getByRole("alert")).toHaveTextContent("Your sign-in expired");
    expect(screen.getByRole("alert")).toHaveTextContent("hz ui");
    await passes(GIVE_UP_MS * 2);
    expect(probe).toHaveBeenCalledTimes(1); // a new link is needed: asking again won't help
    vi.useRealTimers(); // axe waits on real timers
    await expectAccessible(splash());
  });

  it("comes back over the app when hz goes away, and leaves it where it was when hz is back", async () => {
    let answer: ProbeResult = ok;
    const probe = vi.fn(async () => answer);
    const store = createSplashStore(
      [{ type: "mounted" }, { type: "answered" }, { type: "themed" }].reduce(
        (state, event) => reduce(state, event as never),
        initial,
      ),
    );
    const { client } = mount(probe, store);
    const refresh = vi.spyOn(client, "invalidateQueries");
    await passes(1_000);
    expect(splash().hidden).toBe(true);
    answer = { kind: "unreachable" };
    act(() => store.send({ type: "suspect" }));
    await passes(50);
    expect(splash().hidden).toBe(false);
    expect(line()).toBe("Reconnecting to hz…");
    answer = ok;
    await passes(1_000);
    expect(store.get().phase).toBe("ready");
    expect(refresh).toHaveBeenCalled();
    await passes(600);
    expect(splash().hidden).toBe(true);
  });

  it("shows the restart's line until the reload", async () => {
    const { store } = mount(vi.fn(async () => ok));
    act(() => store.send({ type: "themed" }));
    await passes(1_000);
    act(() => store.send({ type: "restart", at: now() }));
    await passes(5_000);
    expect(splash().hidden).toBe(false);
    expect(line()).toBe("Restarting hz…");
    act(() => store.send({ type: "restart-failed" }));
    expect(screen.getByRole("alert")).toHaveTextContent("hz isn't answering");
  });

  it("goes at once when the page has no token, which says how to get one", async () => {
    render(<SplashGone store={createSplashStore()} now={now} />);
    await passes(600);
    expect(splash().hidden).toBe(true);
  });
});
