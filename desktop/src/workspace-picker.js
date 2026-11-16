const api = window.workspacePicker;
const byId = (id) => document.getElementById(id);
let awaitingSignIn = false;
function render(state) {
  byId("status").textContent = state.message;
  byId("status").classList.toggle("problem", state.problem === true);
  byId("progress").hidden = !state.busy;
  byId("retry").hidden = !state.canRetry;
  byId("connection").hidden = !state.current;
  byId("back").hidden = !state.current;
  byId("current").textContent = state.current ?? "";
  byId("version").textContent = state.version ? `Version ${state.version}` : "";
  byId("recents").hidden = state.recent.length === 0;
  byId("recent-list").replaceChildren(...state.recent.map((entry) => {
    const row = document.createElement("div"); row.className = "recent";
    const button = document.createElement("button"); button.className = "row";
    const label = document.createElement("span");
    const name = document.createElement("strong"); name.textContent = entry.name;
    const detail = document.createElement("small"); detail.textContent = entry.signedIn ? entry.origin : `${entry.origin} · Sign-in required`;
    label.append(name, detail); button.append(label);
    button.addEventListener("click", () => act(() => api.recent(entry.origin)));
    button.dataset.current = String(entry.origin === state.current);
    const forget = document.createElement("button"); forget.className = "forget"; forget.textContent = "×";
    forget.title = `Forget ${entry.name}`; forget.setAttribute("aria-label", forget.title);
    forget.addEventListener("click", () => act(() => api.forget(entry.origin)));
    row.append(button, forget); return row;
  }));
  for (const control of document.querySelectorAll("button, input")) control.disabled = state.busy;
  byId("local").disabled = state.busy || Boolean(state.current?.startsWith("http://"));
  for (const button of document.querySelectorAll('[data-current="true"]')) button.disabled = true;
  if (state.needsSignIn && !awaitingSignIn && !state.busy) {
    byId("link").focus();
    byId("link").scrollIntoView({ block: "center" });
  }
  awaitingSignIn = state.needsSignIn;
}
async function act(action) {
  try { render(await action()); } catch { byId("status").textContent = "Connection unavailable. Close this window and try again."; }
}
byId("connect").addEventListener("submit", (event) => {
  event.preventDefault();
  const link = byId("link").value;
  byId("link").value = "";
  void act(() => api.connect(link));
});
byId("local").addEventListener("click", () => act(() => api.local()));
byId("retry").addEventListener("click", () => act(() => api.retry()));
byId("back").addEventListener("click", () => api.back());
api.onState(render);
void act(() => api.state());
