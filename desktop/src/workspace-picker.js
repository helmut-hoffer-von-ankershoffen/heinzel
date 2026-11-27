const api = window.workspacePicker;
const byId = (id) => document.getElementById(id);
let state, expectedOrigin, returnFocus;
let audio = "Unknown";
const phases = { reconnecting: "Reconnecting…", waiting: "Waiting for network", paused: "Paused",
  signin: "Sign in", denied: "Access denied", unavailable: "Unavailable", address: "Check workspace address" };

const element = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};
function button(label, glyph, action, key) {
  const node = element("button", glyph, "row-action");
  node.type = "button"; node.title = label; node.setAttribute("aria-label", label);
  node.dataset.key = key; node.addEventListener("click", action);
  return node;
}
async function run(action) {
  try { const result = await action(); if (result) render(result); }
  catch { byId(byId("sheet").open ? "error" : "status").textContent = "Could not complete the request. Try again."; }
}
function edit(origin, label, trigger) {
  expectedOrigin = origin; returnFocus = trigger?.dataset.key;
  byId("sheet-title").textContent = label;
  byId("link").value = ""; byId("error").textContent = ""; fieldChanged();
  if (!byId("sheet").open) byId("sheet").showModal();
  byId("link").focus();
  void run(() => api.edit());
}
function fieldChanged() {
  const empty = !byId("link").value;
  byId("clear").hidden = empty; byId("paste").hidden = !empty; byId("submit").disabled = empty;
}
function focusKey(key) {
  const node = [...document.querySelectorAll("[data-key]")].find((item) => item.dataset.key === key);
  node?.focus({ preventScroll: true });
}
function closeSheet(cancelled) {
  if (byId("sheet").open) byId("sheet").close();
  byId("link").value = ""; fieldChanged();
  if (cancelled) { void run(() => api.cancel()); focusKey(returnFocus); }
  expectedOrigin = null;
}
function row(entry, duplicates) {
  const local = entry.local, origin = entry.origin;
  const active = Boolean(origin && state.current === origin);
  const phase = active && state.recovery?.origin === origin ? state.recovery.phase : null;
  const title = entry.name;
  const wrap = element("div", "", "row"); wrap.setAttribute("role", "listitem");
  const choose = element("button", "", "choose"); choose.type = "button"; choose.dataset.key = "choose-" + (origin ?? "local");
  if (active) choose.setAttribute("aria-current", "true");
  const icon = element("span", local ? "▣" : "↗", "symbol"); icon.setAttribute("aria-hidden", "true");
  const text = element("span", "", "text"), line = element("span", "", "line");
  const version = entry.version || (active ? state.workspace?.version : null);
  line.append(element("strong", title), element("small", version ? "v" + version.replace(/^v/, "") : "Version unknown"));
  const status = phases[phase] || (active ? "Online" : local && state.localAvailable ? "Running" : "Unknown");
  const source = local ? "Local backend" : entry.source ?? "Manual";
  text.append(line, element("small", [status, source, active ? "Selected" : null].filter(Boolean).join(" · "), "detail"));
  if (duplicates.has(title)) text.append(element("small", origin, "detail"));
  choose.append(icon, text);
  choose.addEventListener("click", () => {
    if (local && phase) { void run(() => api.local()); return; }
    if (phase === "signin" || (!local && !active && !entry.signedIn)) { edit(origin, "Sign in", choose); return; }
    if (active && !phase) { void api.back(); return; }
    if (!active) void run(() => local ? api.local() : api.recent(origin));
  });
  wrap.append(choose);
  if (phase === "paused") wrap.append(button("Resume reconnecting to " + title, "▷", () => run(() => api.resume(origin)), "retry-" + origin));
  else if (["reconnecting", "waiting", "unavailable"].includes(phase)) wrap.append(button("Pause reconnecting to " + title, "Ⅱ", () => run(() => api.pause(origin)), "retry-" + origin));
  else if (active) wrap.append(button("Disconnect from " + title, "⇥", () => run(() => api.disconnect(origin)), "leave-" + origin));
  else if (!local && source === "Manual") wrap.append(button("Remove " + title, "−", () => run(() => api.forget(origin)), "remove-" + origin));
  return wrap;
}
function showFacts(id, entries) {
  byId(id).replaceChildren(...entries.filter(([, value]) => value != null && value !== "").flatMap(([label, value]) => [element("dt", label), element("dd", String(value))]));
}
function render(next) {
  if (!next || (state && next.revision < state.revision)) return;
  state = next;
  if (next.needsSignIn && next.signInOrigin && !next.busy && !byId("sheet").open) {
    const origin = next.signInOrigin;
    const trigger = [...byId("rows").querySelectorAll(".choose")].find((item) => item.dataset.key === "choose-" + origin);
    edit(origin, "Sign in", trigger);
  }
  const focus = document.activeElement?.dataset.key;
  const rows = [{ name: "This Mac", local: true, origin: next.localOrigin, version: next.localVersion }, ...next.recent];
  const names = new Set(), duplicates = new Set();
  for (const item of rows) { if (names.has(item.name)) duplicates.add(item.name); names.add(item.name); }
  byId("rows").replaceChildren(...rows.map((entry) => row(entry, duplicates)));
  if (focus) focusKey(focus);
  byId("status").textContent = next.busy || next.problem || next.canRetry ? next.message : "";
  byId("status").classList.toggle("problem", next.problem === true);
  byId("retry").hidden = !next.canRetry || Boolean(next.recovery);
  if (byId("sheet").open && next.problem) byId("error").textContent = next.message;
  const app = next.application, ws = next.workspace;
  byId("version").textContent = app?.version ? "v" + app.version.replace(/^v/, "") : "";
  showFacts("application", [["Type", app?.type], ["Version", app?.version], ["Location", app?.path], ["IP address", app?.ipAddress ?? "Unknown"]]);
  byId("workspace-title").hidden = !ws;
  showFacts("workspace", ws ? [["Name", ws.name], ["Version", ws.version ?? "Unknown"],
    ["Installed version", ws.version && ws.installedVersion !== ws.version ? ws.installedVersion : null],
    ["Address", ws.origin], ["IP address", ws.ipAddress ?? "Unknown"],
    ["Uptime", Number.isFinite(ws.uptimeSeconds) ? ws.uptimeSeconds + " s" : "Unknown"]] : []);
  const checks = next.checks ?? {};
  byId("checks").replaceChildren(...[["Internet", checks.internet], ["Notifications", checks.notifications],
    ["System audio", audio], ["iCloud", checks.icloud]].map(([label, value]) => {
    const healthy = ["Online", "Available"].includes(value);
    const requestable = label === "Notifications" && ["Ask for workspace access", "Denied in workspace"].includes(value) && !next.recovery;
    const unavailable = ["Offline", "Unreachable"].includes(value);
    const node = element(requestable ? "button" : "span", (healthy ? "✓ " : unavailable ? "⊗ " : "◌ ") + label);
    if (requestable) { node.className = "permission"; node.onclick = () => run(() => api.notifications()); }
    else node.setAttribute("role", "img");
    node.title = label + " · " + (value || "Unknown");
    node.setAttribute("aria-label", node.title);
    if (unavailable) node.className = "problem";
    return node;
  }));
}
byId("add").dataset.key = "add";
byId("add").onclick = () => edit(null, "Add workspace", byId("add"));
byId("cancel").onclick = () => closeSheet(true);
byId("sheet").oncancel = (event) => { event.preventDefault(); closeSheet(true); };
byId("link").oninput = fieldChanged;
byId("clear").onclick = () => { byId("link").value = ""; fieldChanged(); byId("link").focus(); };
byId("paste").onclick = async () => {
  try { const link = await api.paste(); if (byId("sheet").open && link) byId("link").value = link; } catch { /* System paste stays available. */ }
  fieldChanged(); byId("link").focus();
};
byId("form").onsubmit = async (event) => {
  event.preventDefault();
  if (!byId("link").value || state?.busy) return;
  const result = await api.connect(byId("link").value, expectedOrigin).catch(() => null);
  if (!result) { byId("error").textContent = "Could not open the workspace. Try again."; return; }
  render(result);
  if (!result.problem && !result.busy && result.message.startsWith("Connected")) closeSheet(false);
  else if (byId("sheet").open) byId("link").focus();
};
byId("retry").onclick = () => run(() => api.retry());
function information(show) {
  byId("information").hidden = !show; byId("chooser").hidden = show; byId("info").hidden = show;
  (show ? byId("information-title") : byId("info")).focus();
  void run(() => api.information(show));
}
byId("info").onclick = () => information(true);
byId("back").onclick = () => information(false);
window.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || byId("sheet").open) return;
  if (!byId("information").hidden) information(false); else if (!state?.recovery) void api.back();
});
for (const name of ["online", "offline"]) window.addEventListener(name, () => run(() => api.network()));
api.onState(render);
void run(() => api.state());
navigator.mediaDevices?.enumerateDevices().then((devices) => {
  audio = devices.some((device) => device.kind === "audiooutput") ? "Available" : "Unknown";
  if (state) render(state);
}).catch(() => {});
