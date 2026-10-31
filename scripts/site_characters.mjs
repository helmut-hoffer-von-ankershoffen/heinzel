// Regenerate the homepage's original character artwork: node scripts/site_characters.mjs.
import { writeFileSync } from "node:fs";
import { heinzelSvg } from "../web/src/components/heinzel.ts";

const helpers = {
  timekeeper: { age: "young", hat: "teal", shirt: "rose", build: "slim", state: "idle" },
  draftkeeper: { age: "old", hat: "purple", shirt: "slate", build: "slim", state: "thinking" },
  reviewer: { age: "grown", hat: "rust", shirt: "blue", state: "waiting" },
};
for (const [name, look] of Object.entries(helpers)) {
  writeFileSync(new URL(`../docs/assets/website/helper-${name}.svg`, import.meta.url), heinzelSvg(look, "full"));
}
