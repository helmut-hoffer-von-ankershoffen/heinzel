// After a reload, the Chat tab sits below page content that is still loading, so a click can land on what moved under
// the pointer and do nothing. Wait for startup to end, then press the tab: that needs no position. Require that Chat
// really is selected and shown.
export async function revealChat(page) {
  await page.locator("#splash").waitFor({ state: "hidden" });
  await page.waitForFunction(() => {
    const root = document.getElementById("root");
    return root !== null && !root.hasAttribute("inert");
  });
  await page.getByRole("tab", { name: "Chat", exact: true }).press("Enter");
  await page.getByRole("tab", { name: "Chat", exact: true, selected: true }).waitFor();
  await page.getByRole("tabpanel", { name: "Chat", exact: true }).waitFor();
}
