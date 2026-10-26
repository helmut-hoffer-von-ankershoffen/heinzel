import { readFileSync } from "node:fs";
import { join } from "node:path";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { acmeTheme, config, shell, themeList } from "@/test/fixtures";
import { expectAccessible, mockApi } from "@/test/render";
import { GIVE_UP_MS, initial, reduce, type SplashEvent } from "./machine";
import { type ProbeResult, Splash, SplashGone, START } from "./Splash";
import { createSplashStore } from "./store";

const page = readFileSync(join(process.cwd(), "index.html"), "utf8"); // vitest runs in web/
const markup = /<div id="splash"[\s\S]*?<\/div>\s*(?=<script>)/.exec(page)?.[0] ?? "";
const ok: ProbeResult = { kind: "ok", info: { version: "0.9.1" } };
const gone: ProbeResult = { kind: "unreachable" };

let started = 0;
const now = () => Date.now() - started;
const splash = () => document.getElementById("splash") as HTMLElement;
const line = () => document.getElementById("splash-line")?.textContent;
const ready = () =>
  createSplashStore(
    ([{ type: "mounted" }, { type: "answered" }, { type: "themed" }] as SplashEvent[]).reduce(reduce, initial),
  );

function mount(probe: () => Promise<ProbeResult>, store = createSplashStore()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const refresh = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <Splash store={store} probe={probe} now={now} />
    </QueryClientProvider>,
    { container: document.getElementById("root") as HTMLElement },
  );
  return { store, refresh };
}

const passes = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

beforeEach(() => {
  vi.useFakeTimers();
  started = Date.now();
  document.body.innerHTML = `${markup}<div id="root"></div>`;
  mockApi({ ...shell, "/api/system/theme/list": themeList("acme") });
});

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = "";
});

describe("the splash", () => {
  it("connects, opens, then fades out once the theme is on the page, never before its least time", async () => {
    const probe = vi.fn(async () => ok);
    const { store, refresh } = mount(probe);
    await passes(0);
    expect(line()).toBe("Opening…");
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
    expect(refresh).not.toHaveBeenCalled();
    expect(document.getElementById("splash-version")?.textContent).toBe("v0.9.1");
    expect(document.querySelector('#splash-brand img[src^="/theme-assets/"]')).toHaveAttribute(
      "alt",
      acmeTheme.logo?.alt,
    );
  });

  it("signs you in while this browser pairs", async () => {
    const store = createSplashStore();
    store.send({ type: "pairing" });
    mount(
      vi.fn(async () => ok),
      store,
    );
    await passes(0);
    expect(line()).toBe("Signing you in…");
  });

  it("goes without a fade for someone who asked the system for less motion", async () => {
    vi.spyOn(window, "matchMedia").mockImplementation(
      (query: string) => ({ matches: query.includes("reduced-motion") }) as MediaQueryList,
    );
    const { store } = mount(vi.fn(async () => ok));
    act(() => store.send({ type: "themed" }));
    await passes(300);
    expect(splash()).toHaveAttribute("data-leaving");
    await passes(1); // not the 200 ms fade
    expect(splash().hidden).toBe(true);
  });

  it("keeps asking while hz doesn't answer, shows the fix, and the app reads everything again when hz starts", async () => {
    let answer: ProbeResult = gone;
    const probe = vi.fn(async (): Promise<ProbeResult> => answer);
    const { store, refresh } = mount(probe);
    await passes(0);
    expect(line()).toBe("Connecting to hz…");
    await passes(GIVE_UP_MS - 100);
    expect(screen.queryByRole("alert")).toBeNull();
    await passes(200);
    await passes(0); // playful preference query settles before inspecting its optional illustration
    expect(screen.getByRole("alert")).toHaveTextContent("hz isn't answering");
    expect(screen.getByTestId("offline-scene")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Start it in a terminal");
    expect(screen.getByRole("alert")).toHaveTextContent("Trying again every few seconds");
    expect(screen.getByRole("button", { name: `Copy the command ${START}` })).toBeInTheDocument();
    expect(splash().dataset.state).toBe("error");
    answer = ok;
    await passes(1_000);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(refresh).toHaveBeenCalled(); // what failed while hz was away reads again, booted or not
    expect(store.get().phase).toBe("ready"); // it waited long enough: the theme follows when its list is read again
    await passes(500);
    expect(splash().hidden).toBe(true);
  });

  it("hides splash illustrations when playful art is disabled and keeps recovery words", async () => {
    const quiet = {
      ...shell,
      "/api/system/config/show": {
        ...config,
        settings: [
          ...config.settings,
          {
            section: "hz",
            key: "playful",
            value: false,
            default: true,
            source: "user",
            env: "HZ_PLAYFUL",
            description: "",
          },
        ],
      },
    };
    mockApi(quiet);
    let answer: (result: ProbeResult) => void = () => undefined;
    mount(() => new Promise<ProbeResult>((resolve) => (answer = resolve)));
    await passes(0);
    expect(line()).toBe("Connecting to hz…");
    expect(screen.queryByTestId("connection-scene")).toBeNull();
    await act(async () => answer(gone));
    await passes(GIVE_UP_MS + 100);
    expect(screen.queryByTestId("offline-scene")).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("hz isn't answering");
    expect(screen.getByRole("button", { name: `Copy the command ${START}` })).toBeInTheDocument();
  });

  it("shows a Heinzelmännchen searching with a looking glass while connecting", async () => {
    mockApi(shell);
    mount(() => new Promise<ProbeResult>(() => undefined));
    await passes(0);
    expect(screen.getByTestId("connection-scene")).toBeInTheDocument();
    expect(screen.getByTestId("connection-scene").querySelector("svg")).toBeInTheDocument();
  });

  it("doesn't give up before hz was asked, however long the page took to load", async () => {
    started = Date.now() - 2 * GIVE_UP_MS; // the bundle took 16 s
    let answer: (result: ProbeResult) => void = () => undefined;
    mount(() => new Promise<ProbeResult>((resolve) => (answer = resolve)));
    await passes(1_000);
    expect(screen.queryByRole("alert")).toBeNull();
    await act(async () => answer(gone));
    await passes(0);
    expect(screen.getByRole("alert")).toHaveTextContent("hz isn't answering");
  });

  it("says to sign in again, with the command, when hz refuses the token, and stops asking", async () => {
    const probe = vi.fn(async (): Promise<ProbeResult> => ({ kind: "refused" }));
    mount(probe);
    await passes(0);
    expect(screen.getByRole("alert")).toHaveTextContent("Sign in again");
    expect(screen.getByRole("alert")).toHaveTextContent("press Ctrl+C");
    await passes(GIVE_UP_MS * 2);
    expect(probe).toHaveBeenCalledTimes(1); // a new link is needed: asking again won't help
    vi.useRealTimers(); // axe waits on real timers
    await expectAccessible(splash());
  });

  it("ignores a blip, but comes back over the app when hz is gone, and gives focus back when it returns", async () => {
    let answer: ProbeResult = ok;
    const probe = vi.fn(async (): Promise<ProbeResult> => answer);
    const store = ready();
    const { refresh } = mount(probe, store);
    await passes(1_000);
    expect(splash().hidden).toBe(true);
    const input = document.createElement("input"); // the chat's box, say
    document.getElementById("root")?.append(input);
    input.focus();
    answer = gone;
    probe.mockImplementationOnce(async () => gone).mockImplementationOnce(async () => ok);
    act(() => store.send({ type: "suspect" }));
    await passes(600);
    expect(store.get().phase).toBe("ready"); // one failed answer, then hz was there: a blip
    act(() => store.send({ type: "suspect" }));
    await passes(600);
    expect(splash().hidden).toBe(false);
    expect(line()).toBe("Reconnecting to hz…");
    answer = ok;
    await passes(1_000);
    expect(store.get().phase).toBe("ready");
    expect(refresh).toHaveBeenCalled();
    await passes(600);
    expect(splash().hidden).toBe(true);
    expect(document.activeElement).toBe(input);
  });

  it("gives up on a lost hz GIVE_UP_MS after it went, not after the page loaded", async () => {
    const store = ready();
    mount(
      vi.fn(async () => gone),
      store,
    );
    await passes(20_000);
    act(() => store.send({ type: "suspect" }));
    await passes(600 + GIVE_UP_MS - 200);
    expect(line()).toBe("Reconnecting to hz…");
    await passes(400);
    expect(screen.getByRole("alert")).toHaveTextContent("hz isn't answering");
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
