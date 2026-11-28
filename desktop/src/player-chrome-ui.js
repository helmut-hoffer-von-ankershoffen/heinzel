/* Dedicated local renderer: no product bridge, URLs, markup or scripts from the provider. */
const bridge = window.playerChrome;
const status = document.getElementById("status");
const send = (request) => bridge.command(request).catch(() => { status.textContent = "Player controls unavailable"; });
for (const button of document.querySelectorAll("[data-action]")) {
  button.addEventListener("click", () => send({ action: button.dataset.action }));
}
for (const [id, action] of [["volume", "volume"], ["width", "resize"]]) {
  document.getElementById(id).addEventListener("change", (event) => send({ action, value: Number(event.target.value) }));
}
const move = document.getElementById("move");
let start;
move.addEventListener("pointerdown", (event) => {
  start = { x: event.screenX, y: event.screenY };
  move.setPointerCapture(event.pointerId);
});
move.addEventListener("pointerup", (event) => {
  if (start) send({ action: "move", x: event.screenX - start.x, y: event.screenY - start.y });
  start = null;
});
move.addEventListener("pointercancel", () => { start = null; });
move.addEventListener("keydown", (event) => {
  const delta = { ArrowLeft: [-16, 0], ArrowRight: [16, 0], ArrowUp: [0, -16], ArrowDown: [0, 16] }[event.key];
  if (delta) { event.preventDefault(); send({ action: "move", x: delta[0], y: delta[1] }); }
});
