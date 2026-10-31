# Screenshot playbook for humans and agents

Show what someone will gain by trying Heinzel. Capture the real product with fictional data, then review the exact
images before publishing. Use this playbook when planning a new gallery or refreshing an existing one.

## Start with a story

Read the owning feature's source and public documentation. Write down its audience, useful outcome, visible
surfaces, fixture data and acceptance criteria before running it. Choose a populated overview, a readable detail
and useful CLI output. Include mobile where the feature changes layout. Settings are supporting material.

For example, World Clock has more to show than its settings: Today, the ticker, footer, sidebar globe and CLI.
Resource monitoring needs the expanded CPU, memory and load view. Weather needs a populated Today forecast.
Themes need applied appearances. Terminal, chat and workflow screenshots must show actual interactions and
results, with captions that distinguish demonstration data from live accounts.

Use a few distinct scenes rather than many nearly identical images. Crop with the capture tool's native element
focus, keeping enough context to explain the detail. Choose proportions for readability; do not force phone views
and terminal output into one aspect ratio. Never redraw a product screen or edit pixels to invent functionality.

## Capture the intended state

- **Wait for the feature, not a timer.** Wait for its accessible region, populated content and required renderer.
  For a globe, explicitly request `reduced_motion = "no-preference"` and a `wait-webgl` step. Reduced motion can
  intentionally select a flat fallback; a screenshot of that fallback does not prove the globe rendered.
- **Wait for every captioned contribution.** Readiness of one widget does not prove that the ticker, footer,
  sidebar or panel is populated. Match each claimed surface to its own visible content and renderer before
  saving. An independent World Clock review caught missing lazy ticker/footer content after the main widget
  was ready; the capture must wait for all the surfaces its caption promises.
- **Clear accidental interaction states.** After necessary clicks, move the pointer to a neutral, noninteractive
  region. Check hover underlines, tooltips, focus rings and selected rows. Preserve a state only when it is part
  of the story; never remove legitimate keyboard focus from an accessibility demonstration.
- **Explain what each marker means.** Compare markers with the fixture and source. World Clock's yellow subsolar
  marker represents the Sun's overhead position, not another city. A three-city fixture can therefore have four
  markers. If a marker is ambiguous, prefer a clearer scene or explain it accurately in the caption.
- **Check differences between rows.** At a fixed time, some locations can be working while others are off duty.
  Verify the local times, work hours, icons and countdowns. Do not mistake an accidental hover underline for an
  intentional off-duty style, or erase a real state difference for visual uniformity.
- **Use deterministic data.** Fix the demonstration date and time, use obvious example identities, and record
  the SDK version. Capture against the intended built app, not an unrelated development server.

These choices belong in the editable scene plan and source-based story, so the next capture repeats them.
For the plugin toolkit, consult `docs/SCREENSHOT_AUTHORING.md` in its owning checked-out repository and record the
checkout revision. Verify that each command exists in the installed toolkit version before using it. Capture
workflow changes can be under review independently of published gallery assets.

## Review privacy and usefulness

Use a disposable HOME and application profile with fictional local fixtures, no inherited credentials and no
external writes. Inspect fixture code before executing it. Authentication URLs, session fragments and terminal
output can expose tokens even when the rest of the screen looks harmless. Redact credentials before every image
save, including failure diagnostics; do not rely on a later visual cleanup.

An independent reviewer must open every final image and read every transcript and caption. Review privacy,
truthfulness, useful feature coverage, legibility and accidental interaction states together. A pattern scan
supports this review but cannot approve it. Bind approval to the exact image, transcript, plan and source hashes.
Any new crop, pixel change, transcript normalization or capture requires renewed review of the changed bundle.
Keep private review logs, failure captures and unapproved drafts out of public repositories. Publish only approved
assets and bounded provenance.

## Verify the rendered page

Check the built page in Chromium and WebKit at desktop and phone sizes, in light and dark appearances:

- Images load at readable sizes; closeups reveal something useful. No horizontal page overflow or clipped captions.
- Galleries swipe sideways while vertical gestures still scroll the page. Buttons, keyboard navigation, counters,
  zoom, Escape and focus return work. Native scrolling and original-image links remain useful without JavaScript.
- Characters lead with the illustration, a name, one short line and a useful link. Desktop gutter placement must
  clear navigation, article, table of contents and footer; phone layouts keep characters inline.
- Lazy images are scrolled into view before readiness checks. Wait with a deadline for `complete` and
  `naturalWidth > 0` before decoding. This avoids mistaking a WebKit lazy-source transition for a missing image.
- Generated Markdown containing nested raw HTML is verified after Markdown conversion. Check the actual DOM and
  image geometry; a source string assertion cannot catch a collapsed image or prematurely closed wrapper.

## Publish in reviewable increments

Publish one coherent gallery or page improvement through the repository's normal review and checks. GitHub Pages
deploys through its documentation workflow independently of installer releases. Do not trigger an application
release merely to update static screenshots. Verify the completed deployment, live catalog counts, image loading
and representative mobile interactions before reporting it live.

Record what shipped, the immutable source revisions, remaining work and exact pickup paths in the handoff. Preserve
unpublished work separately; do not publish a mixed worktree wholesale. Reuse approved immutable image bytes across
Pages, READMEs and app galleries while respecting each surface's implementation and review status.
