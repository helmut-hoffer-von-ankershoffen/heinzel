// The import dialog's page: three views, plain DOM, text only (no HTML from data). It never sees a cookie value.
"use strict";
(() => {
  const api = window.cookieImport;
  const $ = (id) => document.getElementById(id);
  const LABELS = { email: "Email", payments: "Bank or payments", "sign-in": "Sign-in provider" };
  let sites = [];
  const chosen = new Set();
  const show = (view) => { for (const id of ["start", "choose", "manage"]) $(id).hidden = id !== view; };
  const status = (id, text, problem = false) => { $(id).textContent = text; $(id).classList.toggle("problem", problem); };
  const reason = (error) => String(error?.message ?? error).replace(/^Error invoking remote method '[^']+': (Error: )?/, "");
  const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

  async function start() {
    show("start");
    status("start-status", "");
    const select = $("profile");
    select.replaceChildren();
    const profiles = await api.profiles().catch(() => []);
    for (const profile of profiles) select.append(new Option(profile.label, profile.id));
    $("next").disabled = profiles.length === 0;
    if (!profiles.length) status("start-status", "No Google Chrome profile was found on this Mac.", true);
  }

  function visible() {
    const filter = $("filter").value.trim().toLowerCase();
    return sites.filter((entry) => !filter || entry.site.includes(filter));
  }

  function render() {
    const list = $("sites");
    list.replaceChildren();
    for (const entry of visible()) {
      const row = document.createElement("li");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = chosen.has(entry.site);
      box.setAttribute("aria-label", entry.site);
      box.addEventListener("change", () => {
        if (box.checked) chosen.add(entry.site);
        else chosen.delete(entry.site);
        count();
      });
      const name = document.createElement("span");
      name.className = "site";
      name.textContent = entry.site;
      row.append(box, name);
      if (entry.category) {
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.textContent = LABELS[entry.category] ?? entry.category;
        row.append(badge);
      }
      const total = document.createElement("span");
      total.className = "count";
      total.textContent = String(entry.count);
      row.append(total);
      list.append(row);
    }
    count();
  }

  function count() {
    $("selected").textContent = `${chosen.size} of ${sites.length} selected`;
    $("all").checked = sites.length > 0 && chosen.size === sites.length;
    $("import").disabled = chosen.size === 0;
  }

  async function choose() {
    $("next").disabled = true;
    status("start-status", "Reading the list of sites…");
    try {
      sites = await api.sites($("profile").value);
      chosen.clear();
      for (const entry of sites) if (entry.checked) chosen.add(entry.site);
      $("filter").value = "";
      status("choose-status", "");
      show("choose");
      render();
    } catch (error) { status("start-status", reason(error), true); }
    finally { $("next").disabled = false; }
  }

  async function importChosen() {
    $("import").disabled = true;
    $("back").disabled = true;
    status("choose-status", "Importing… macOS may ask for your Mac login password.");
    try {
      const result = await api.import($("profile").value, [...chosen], $("persistent").checked);
      const skipped = result.skipped + result.failed;
      status("choose-status", `Imported ${plural(result.imported, "cookie")} from ${plural(result.sites, "site")}.` +
        (skipped ? ` ${plural(skipped, "cookie")} could not be imported.` : ""));
    } catch (error) { status("choose-status", reason(error), true); }
    finally { $("back").disabled = false; count(); }
  }

  async function manage() {
    show("manage");
    status("manage-status", "");
    const list = $("imported");
    list.replaceChildren();
    const entries = await api.imported().catch(() => []);
    $("remove-all").disabled = entries.length === 0;
    if (!entries.length) status("manage-status", "No imported cookies.");
    for (const entry of entries) {
      const row = document.createElement("li");
      const name = document.createElement("span");
      name.className = "site";
      name.textContent = entry.site;
      const total = document.createElement("span");
      total.className = "count";
      total.textContent = plural(entry.count, "cookie");
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "danger";
      remove.textContent = "Remove";
      remove.setAttribute("aria-label", `Remove cookies of ${entry.site}`);
      remove.addEventListener("click", async () => { await api.remove(entry.site).catch(() => {}); await manage(); });
      row.append(name, total, remove);
      list.append(row);
    }
  }

  $("all").addEventListener("change", () => {
    if ($("all").checked) for (const entry of sites) chosen.add(entry.site); else chosen.clear();
    render();
  });
  $("filter").addEventListener("input", render);
  $("next").addEventListener("click", choose);
  $("import").addEventListener("click", importChosen);
  $("back").addEventListener("click", () => void start());
  $("cancel").addEventListener("click", () => void api.close());
  $("done").addEventListener("click", () => void api.close());
  $("manage-link").addEventListener("click", () => void manage());
  $("remove-all").addEventListener("click", async () => { await api.removeAll().catch(() => {}); await manage(); });
  api.onView((view) => void (view === "manage" ? manage() : start()));
  void start();
})();
