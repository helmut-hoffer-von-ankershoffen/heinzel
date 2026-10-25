// Real CSS hit testing over a live iframe. The local canvas stream avoids external playback/network requirements.
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
  const results = [];
  async function front(name, pane) {
    await pane.waitFor();
    const box = await pane.boundingBox();
    if (!box) throw new Error(`${name}: no pane bounds`);
    // Put the iframe's picture (not only the card header) under the pane's middle.
    await card.evaluate((element, box) => {
      Object.assign(element.style, {
        left: `${Math.max(0, box.x + box.width / 2 - 144)}px`,
        top: `${Math.max(0, box.y + box.height / 2 - 110)}px`,
        bottom: "auto",
        width: "288px",
      });
    }, box);
    const visible = await pane.evaluate(
      (element, box) => element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)),
      box,
    );
    if (!visible) {
      const hit = await page.evaluate(
        (box) => document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)?.outerHTML,
        box,
      );
      if (screenshot) await page.screenshot({ path: `${shots}/layers-failure.png` });
      throw new Error(`${name}: the pane is obscured by ${hit}`);
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
    await page.getByRole("button", { name: "Sound options" }).click();
    await front(`${mode}-audio`, page.getByRole("dialog", { name: "Sound", exact: true }));
    await page.keyboard.press("Escape");
    await page.mouse.move(0, 0);
    await park();
    await page.getByRole("contentinfo").locator("button").first().click();
    await front(`${mode}-version`, page.getByRole("region", { name: /^v\d/ }));
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
