// Regenerate the homepage's original character artwork: node scripts/site_characters.mjs.
import { writeFileSync } from "node:fs";
import { heinzelSvg } from "../web/src/components/heinzel.ts";

const helpers = {
  timekeeper: { age: "young", hat: "blue", shirt: "teal", build: "slim", state: "idle" },
  draftkeeper: { age: "grown", hat: "teal", shirt: "rose", state: "thinking" },
  reviewer: { age: "old", hat: "slate", shirt: "ochre", state: "waiting" },
};
for (const [name, look] of Object.entries(helpers)) {
  writeFileSync(new URL(`../docs/assets/website/helper-${name}.svg`, import.meta.url), heinzelSvg(look, "full"));
}
