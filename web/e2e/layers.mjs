// Real CSS hit testing over a live iframe. The local canvas stream avoids external playback/network requirements.
let stabilityCheck = 0;

export async function verifyLayers(page, shots, screenshot) {
  const stream = {
    provider: "youtube",
    video: "tRsQsTMvPNg",
    frame_origin: "https://www.youtube-nocookie.com",
    embed_url: "https://www.youtube-nocookie.com/embed/tRsQsTMvPNg",
    watch_url: "https://www.youtube.com/watch?v=tRsQsTMvPNg",
  };
  const sound = {
    name: "layer-probe",
    title: "Layer probe",
    kind: "stream",
    stream,
    module: null,
    plugin: null,
    selected: true,
    effective: true,
    fade_seconds: 0,
  };
  await page.route("**/api/system/sound/list", (route) =>
    route.fulfill({
      json: {
        selected: sound.name,
        effective: sound.name,
        soundscapes: [sound],
        signals: {},
        volume: 50,
        follow_theme: false,
        mute_in_meetings: false,
        duck_while_typing: false,
      },
    }),
  );
  await page.route("https://www.youtube-nocookie.com/embed/**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html><body style="margin:0"><video muted autoplay playsinline style="width:100%"></video><script>
    const canvas=document.createElement('canvas');canvas.width=320;canvas.height=180;
    const ctx=canvas.getContext('2d');let i=0;setInterval(()=>{ctx.fillStyle=i++%2?'#c06030':'#308090';ctx.fillRect(0,0,320,180)},40);
    const video=document.querySelector('video');video.srcObject=canvas.captureStream(25);video.play();
    addEventListener('message',()=>parent.postMessage(JSON.stringify({event:'onReady'}),new URL(document.referrer).origin));
    </script></body></html>`,
    }),
  );
  await page.route("**/api/system/ui/status**", (route) =>
    route.fulfill({
      json: [
        {
          id: "system:daemon",
          module: "system",
          plugin: null,
          order: 1,
          refresh: 60,
          slots: { footer: true },
          problem: null,
          item: {
            text: "Daemon running",
            tone: "ok",
            icon: "bot",
            detail: [{ text: "Version 0.7.2", tone: "neutral" }],
          },
        },
      ],
    }),
  );
  await page.reload();
  await page.getByRole("button", { name: "Play Layer probe", exact: true }).click();
  const card = page.getByRole("region", { name: "Layer probe player" });
  const iframe = card.locator("iframe");
  await iframe.waitFor();
  await page.evaluate(() => {
    window.layerProofFrame = document.querySelector("[data-dock] iframe");
  });
  const frame = await (await iframe.elementHandle()).contentFrame();
  await frame.waitForFunction(() => document.querySelector("video")?.currentTime > 0.1);
  const before = await frame.locator("video").evaluate((video) => video.currentTime);
  // The application uses its main/nav scrollers. A browser must never scroll the whole shell and strand its
  // account menu above the viewport (the Linux runner reproduced scrollY=710 after starting docked playback).
  await page.evaluate(() => {
    const shell = document.querySelector("main").closest(".h-dvh");
    shell.scrollTop = 710;
    window.scrollTo(0, document.documentElement.scrollHeight);
  });
  await page.waitForFunction(
    () => scrollY === 0 && document.querySelector("main").closest(".h-dvh").scrollTop === 0,
    null,
    { timeout: 5000 },
  );
  const results = [];
  async function front(name, pane) {
    await pane.waitFor();
    // The layer assertion is about geometry and hit testing. Some CI hosts keep an optional font request
    // pending even after the UI is usable; the stable-rectangle check below is the relevant readiness gate.
    const stabilityKey = `__hzStablePane_${++stabilityCheck}`;
    await pane.evaluate((element, key) => element.setAttribute("data-hz-e2e-stability", key), stabilityKey);
    try {
      await page.waitForFunction(
        (element) => {
          const box = element.getBoundingClientRect();
          const inside =
            box.width > 0 &&
            box.height > 0 &&
            box.x >= 0 &&
            box.y >= 0 &&
            box.right <= innerWidth &&
            box.bottom <= innerHeight;
          const current = { x: box.x, y: box.y, width: box.width, height: box.height };
          const key = element.getAttribute("data-hz-e2e-stability");
          const previous = window[key];
          const same =
            previous && Object.keys(current).every((name) => Math.abs(current[name] - previous.rect[name]) < 0.5);
          const since = inside && same ? previous.since : performance.now();
          window[key] = { rect: current, since };
          // Wait for final positioning after content has settled, not a transient frame above the footer.
          return inside && performance.now() - since >= 150;
        },
        await pane.elementHandle(),
        { timeout: 5000 },
      );
    } catch {
      const geometry = await pane.evaluate((element) => {
        const ancestors = [];
        let parent = element.parentElement;
        while (parent && ancestors.length < 8) {
          const style = getComputedStyle(parent);
          ancestors.push({
            tag: parent.tagName,
            classes: parent.className,
            rect: parent.getBoundingClientRect().toJSON(),
            scrollTop: parent.scrollTop,
            scrollHeight: parent.scrollHeight,
            clientHeight: parent.clientHeight,
            overflow: style.overflow,
            display: style.display,
            height: style.height,
          });
          parent = parent.parentElement;
        }
        return {
          pane: element.getBoundingClientRect().toJSON(),
          paneStyle: {
            top: getComputedStyle(element).top,
            maxHeight: getComputedStyle(element).maxHeight,
          },
          trigger: document.querySelector("footer")?.getBoundingClientRect().toJSON(),
          ancestors,
          viewport: { width: innerWidth, height: innerHeight },
          scroll: { x: scrollX, y: scrollY },
        };
      });
      if (screenshot) await page.screenshot({ path: `${shots}/${name}-unstable.png` });
      throw new Error(`${name}: pane must fit the viewport before overlap is tested: ${JSON.stringify(geometry)}`);
    } finally {
      await pane
        .evaluate((element, key) => {
          element.removeAttribute("data-hz-e2e-stability");
          delete window[key];
        }, stabilityKey)
        .catch(() => {});
    }
    // Measure, reposition and hit-test in one browser evaluation. Changing a docked card's width would resize its
    // reserved sidebar slot and invalidate a previously captured pane rectangle on the next observer callback.
    const result = await pane.evaluate((element) => {
      const media = document.querySelector("[data-dock]");
      const frame = media.querySelector("iframe");
      const box = element.getBoundingClientRect(),
        card = media.getBoundingClientRect(),
        picture = frame.getBoundingClientRect();
      const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
      Object.assign(media.style, {
        left: `${point.x - picture.width / 2 - (picture.x - card.x)}px`,
        top: `${point.y - picture.height / 2 - (picture.y - card.y)}px`,
        bottom: "auto",
      });
      const after = frame.getBoundingClientRect(),
        hit = document.elementFromPoint(point.x, point.y);
      const overlaps = point.x >= after.x && point.x <= after.right && point.y >= after.y && point.y <= after.bottom;
      return {
        visible: element.contains(hit),
        overlaps,
        point,
        pane: box.toJSON(),
        picture: after.toJSON(),
        viewport: { width: innerWidth, height: innerHeight },
        hit: hit?.tagName,
      };
    });
    if (!result.visible || !result.overlaps) {
      if (screenshot) await page.screenshot({ path: `${shots}/layers-failure.png` });
      throw new Error(`${name}: overlapping picture must remain below the visible pane: ${JSON.stringify(result)}`);
    }
    results.push(name);
    if (screenshot) await page.screenshot({ path: `${shots}/layers-${name}.png` });
  }
  const aside = page.locator("aside").first();
  async function park() {
    await card.evaluate((element) => Object.assign(element.style, { left: "600px", top: "80px", bottom: "auto" }));
  }
  for (const mode of ["docked", "floating"]) {
    if (mode === "floating") await page.getByRole("button", { name: "Pop the player out of the sidebar" }).click();
    await park();
    await aside.locator('button[aria-haspopup="menu"]').click();
    await front(`${mode}-account`, page.getByRole("menu"));
    await page.keyboard.press("Escape");
    await page.mouse.move(0, 0);
    await park();
    // Open options through the visible speaker's supported context-menu gesture. Long-press timers depend on
    // pointer scheduling and have flaked on macOS runners; context-menu exercises the same visible overlay path.
    await page.getByRole("button", { name: "Pause Layer probe", exact: true }).click({ button: "right" });
    await front(`${mode}-audio`, page.getByRole("dialog", { name: "Sound", exact: true }));
    await page.keyboard.press("Escape");
    await page.mouse.move(0, 0);
    await park();
    const changelogResponse =
      mode === "docked"
        ? page.waitForResponse((response) => {
            const url = new URL(response.url());
            return (
              url.pathname === "/api/system/changelog" &&
              url.searchParams.get("newer") === "false" &&
              response.request().method() === "GET"
            );
          })
        : Promise.resolve();
    await page.getByRole("contentinfo").locator("button").first().click();
    const versionPane = page.getByRole("region", { name: /^v\d/ });
    const response = await changelogResponse;
    await response?.finished();
    await versionPane.locator('[aria-busy="false"]').waitFor();
    await front(`${mode}-version`, versionPane);
    await page.keyboard.press("Escape");
    await page.mouse.move(0, 0);
    await park();
    const clock = page.getByRole("button", { description: "World Clock: time for the team and stakeholders" });
    await clock.click();
    await front(`${mode}-clock`, page.getByRole("region", { name: "World Clock: time for the team and stakeholders" }));
    await page.keyboard.press("Escape");
    await page.mouse.move(0, 0);
    await park();
    await page
      .getByRole("region", { name: "World Clock: time for the team and stakeholders" })
      .waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "Daemon running, OK" }).click();
    await front(`${mode}-daemon`, page.getByRole("region", { name: "Daemon running" }));
    await page.keyboard.press("Escape");
    await page.mouse.move(0, 0);
    await park();
    await page.keyboard.press("ControlOrMeta+,");
    await front(`${mode}-settings`, page.getByRole("dialog", { name: "Settings", exact: true }));
    await page.keyboard.press("Escape");
    await page.mouse.move(0, 0);
    await park();
  }
  // CI found the account anchor near the upper edge. A popup must flip/clamp rather than assume room above it;
  // also exercise a genuinely short viewport, keeping the same playing iframe under the menu.
  const accountTrigger = aside.locator('button[aria-haspopup="menu"]');
  const previousStyle = await accountTrigger.evaluate((element) => element.parentElement.style.cssText);
  for (const height of [900, 360]) {
    await page.setViewportSize({ width: 1440, height });
    await accountTrigger.evaluate((element) =>
      Object.assign(element.parentElement.style, { position: "fixed", top: "80px", left: "8px", width: "240px" }),
    );
    await park();
    await accountTrigger.click();
    await front(`account-upper-anchor-${height}`, page.getByRole("menu"));
    await page.keyboard.press("Escape");
  }
  await accountTrigger.evaluate((element, original) => {
    element.parentElement.style.cssText = original;
  }, previousStyle);
  await page.setViewportSize({ width: 1440, height: 900 });
  await park();
  await page.getByRole("button", { name: "Dock the player in the sidebar" }).click();
  await page.getByRole("button", { name: "Collapse navigation" }).click();
  await page.waitForFunction(
    () => {
      const sidebar = document.querySelector("aside");
      return sidebar && sidebar.getBoundingClientRect().width <= 57;
    },
    undefined,
    { timeout: 5000 },
  );
  const account = aside.locator('button[aria-haspopup="menu"]');
  await account.waitFor();
  const layout = await account.evaluate((element) => {
    const cluster = element.parentElement,
      bounds = cluster.getBoundingClientRect();
    const controls = [...cluster.querySelectorAll(":scope > button, :scope > div > button")];
    return {
      allBelowDivider: controls.every((control) => control.getBoundingClientRect().top >= bounds.top),
      allInside: controls.every((control) => control.getBoundingClientRect().bottom <= bounds.bottom),
      profileLast: element === cluster.lastElementChild,
      initial: element.textContent.trim(),
    };
  });
  if (!layout.allBelowDivider || !layout.allInside || !layout.profileLast || !layout.initial)
    throw new Error(`Compact controls overflow: ${JSON.stringify(layout)}`);
  const compactLayouts = [{ width: 1440, ...layout }];
  for (const width of [768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    const bounded = await account.evaluate((element) => {
      const cluster = element.parentElement,
        bounds = cluster.getBoundingClientRect();
      return [...cluster.querySelectorAll(":scope > button, :scope > div > button")].every((control) => {
        const box = control.getBoundingClientRect();
        return (
          box.top >= bounds.top &&
          box.bottom <= bounds.bottom &&
          box.bottom <= innerHeight &&
          box.left >= 0 &&
          box.right <= innerWidth
        );
      });
    });
    if (!bounded) throw new Error(`Compact controls outside ${width}px viewport`);
    compactLayouts.push({ width, bounded });
    if (screenshot) await page.screenshot({ path: `${shots}/layers-collapsed-${width}.png` });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "Expand navigation" }).click();
  await page.waitForFunction(() => window.layerProofFrame === document.querySelector("[data-dock] iframe"));
  await frame.waitForFunction((before) => document.querySelector("video").currentTime > before + 0.1, before);
  return {
    panes: results,
    layout,
    compactLayouts,
    sameFrame: await page.evaluate(() => window.layerProofFrame === document.querySelector("[data-dock] iframe")),
    playbackAdvanced: true,
  };
}
