import { describe, expect, it } from "vitest";
import { controlFor, type JsonSchema } from "./settingSchema";

// Every shape a setting with known values takes (ADR 0055): a choice, never a text field.
const known: [string, JsonSchema][] = [
  ["a Literal", { type: "string", enum: ["text", "json"] }],
  ["an optional Literal", { anyOf: [{ type: "string", enum: ["a", "b"] }, { type: "null" }] }],
  [
    "a binding",
    {
      anyOf: [{ type: "string" }, { type: "null" }],
      "x-options": { source: "adapters", capability: "tracker", none: "none" },
    },
  ],
  ["a theme", { type: "string", "x-options": { source: "themes" } }],
  ["a layer", { anyOf: [{ type: "string" }, { type: "null" }], "x-options": { source: "layers" } }],
  ["modules", { type: "array", items: { type: "string" }, "x-options": { source: "modules" } }],
  ["a time zone", { type: "string", format: "iana-timezone" }],
  ["hours", { type: "integer", minimum: 0, maximum: 24 }],
];

describe("a setting with known values", () => {
  it.each(known)("%s is never a text field", (_, schema) => {
    expect(controlFor(schema).kind).not.toBe("text");
  });

  it("takes its values from the source the schema names", () => {
    expect(controlFor(known[2]?.[1])).toEqual({
      kind: "options",
      from: { source: "adapters", capability: "tracker", none: "none" },
      multiple: false,
    });
    expect(controlFor(known[5]?.[1])).toMatchObject({ kind: "options", multiple: true });
  });

  it("free text stays text", () => {
    expect(controlFor({ type: "string" }).kind).toBe("text");
  });
});
