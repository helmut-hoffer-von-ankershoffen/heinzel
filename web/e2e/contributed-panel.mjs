// A synthetic contribution exercises the host contract without any installed feature plugin.
export async function contributePanel(page) {
  const bundle = `/plugin-assets/specimen/inspector/${"a".repeat(64)}/index.js`;
  await page.route("**/api/system/ui/views**", (route) =>
    route.fulfill({
      json: [
        {
          id: "specimen:inspector",
          module: "specimen",
          plugin: "heinzel-unforeseen",
          title: "Specimen",
          fallback: "Specimen is unavailable",
          slot: "panel",
          order: 500,
          refresh: 60,
          abi: 1,
          section: null,
          route: null,
          bundle,
          styles: [],
          actions: {},
          source: null,
          problem: null,
        },
      ],
    }),
  );
  await page.route("**/api/system/ui/actions**", (route) =>
    route.fulfill({
      json: [
        {
          id: "specimen:inspect",
          module: "specimen",
          plugin: "heinzel-unforeseen",
          order: 500,
          refresh: 60,
          problem: null,
          slots: { header: true },
          label: "Specimen",
          icon: "star",
          badge: 0,
          effect: { kind: "navigate", path: "/?panel=specimen:inspector" },
        },
      ],
    }),
  );
  await page.route(`**${bundle}`, (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `
    export default sdk => function Specimen({onClose}) {
      const e=sdk.React.createElement;
      return e("section",{"aria-label":"Specimen"},
        e("header",{style:{display:"flex",justifyContent:"space-between",padding:12}},
          e("h2",null,"Specimen"),e(sdk.Button,{onClick:onClose,"aria-label":"Close Specimen"},"Close")),
        e("div",{style:{minHeight:620,padding:16}},"A synthetic contributed panel"));
    }`,
    }),
  );
}
