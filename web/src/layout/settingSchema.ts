// A setting's JSON Schema (`SettingInfo.json_schema`, pydantic's, with every $ref inlined) read for one purpose:
// which control edits it, under which label, and whether it is listed at all (docs/SETTINGS_UX.md has the keys). And
// the TOML text `hz system config set` takes for a value the control produced.
import { inWords } from "./settingsSections";

export type JsonSchema = {
  type?: string | string[];
  enum?: unknown[];
  const?: unknown;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  format?: string;
  items?: JsonSchema;
  prefixItems?: JsonSchema[];
  minItems?: number;
  maxItems?: number;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  default?: unknown;
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  title?: string;
  deprecated?: boolean;
  description?: string;
  examples?: unknown[];
  writeOnly?: boolean;
  /** What each choice of an `enum` is called. */
  "x-labels"?: Record<string, string>;
  /** What a number counts: `seconds`, `usd`, `days`, …; `hour-of-day` for hours shown in the viewer's clock. */
  "x-unit"?: string;
  /** Listed only in expert mode. */
  "x-expert"?: boolean;
  /** Never listed: another control edits it. */
  "x-hidden"?: boolean;
  /** Choices the app looks up, e.g. `{ source: "modules" }`. */
  "x-options"?: { source?: string } & Record<string, unknown>;
};

export const TIMEZONE = "iana-timezone";
export const HOUR = "hour-of-day";
/** A range this small is a list of choices (the hours of a day are 25). */
const FEW = 48;

/** `X | None` is `anyOf: [X, {type: null}]`: the schema of X, with what the field adds around it (its title, help,
 *  default, format and `x-` keys). A union of real types stays as it is. */
export function unwrap(schema: JsonSchema | undefined): JsonSchema | undefined {
  const branches = schema?.anyOf ?? schema?.oneOf;
  if (!schema || !branches) return schema;
  const real = branches.filter((branch) => branch.type !== "null");
  if (real.length !== 1) return schema;
  const { anyOf: _any, oneOf: _one, ...around } = schema;
  return { ...real[0], ...around };
}

const typeOf = (schema: JsonSchema) => (Array.isArray(schema.type) ? undefined : schema.type);

export type Control =
  | { kind: "switch" }
  | { kind: "choice"; options: (string | number)[]; labels?: Record<string, string>; hours?: boolean }
  | {
      kind: "number";
      integer: boolean;
      min?: number;
      max?: number;
      /** The bounds themselves are left out: "more than 0". */
      exclusiveMin?: boolean;
      exclusiveMax?: boolean;
      unit?: string;
    }
  | { kind: "timezone" }
  | { kind: "range"; options: number[]; hours: boolean }
  | { kind: "duration" }
  | { kind: "path" }
  | { kind: "secret" }
  | { kind: "multi"; source: string; capability?: string }
  | { kind: "options"; source: string; capability?: string; none?: string }
  | { kind: "chips" }
  | { kind: "table"; item: JsonSchema; columns: [string, JsonSchema][] }
  | { kind: "text"; input: "text" | "url" | "email"; placeholder?: string };

/** Sources of `x-options` this app can look up itself. */
export const OPTION_SOURCES = ["modules", "adapters"];

/** The whole numbers from `schema`'s bounds, when there are few enough to choose from. */
function steps(schema: JsonSchema): number[] | null {
  if (typeOf(schema) !== "integer") return null;
  const min = schema.minimum ?? (schema.exclusiveMinimum !== undefined ? schema.exclusiveMinimum + 1 : undefined);
  const max = schema.maximum ?? (schema.exclusiveMaximum !== undefined ? schema.exclusiveMaximum - 1 : undefined);
  if (min === undefined || max === undefined || max < min || max - min > FEW) return null;
  return Array.from({ length: max - min + 1 }, (_, index) => min + index);
}

const TEXT_INPUTS: Record<string, "url" | "email"> = { uri: "url", url: "url", email: "email" };

/** The first example as a field's placeholder, when it is a plain value. */
const placeholderOf = (schema: JsonSchema) => {
  const example = schema.examples?.[0];
  return typeof example === "string" || typeof example === "number" ? String(example) : undefined;
};

/** Hours of a day: said so (`x-unit: hour-of-day`), or a pair of whole numbers from 0 to 24. */
const isHours = (schema: JsonSchema, options: number[]) =>
  schema["x-unit"] === HOUR || (options[0] === 0 && options.at(-1) === 24);

/** The control for a value of `raw`, or text when the schema says too little. */
export function controlFor(raw: JsonSchema | undefined | null): Control {
  const schema = unwrap(raw ?? undefined);
  if (!schema) return { kind: "text", input: "text" };
  const type = typeOf(schema);
  if (schema.writeOnly || schema.format === "password") return { kind: "secret" };
  if (schema.enum?.every((value) => typeof value === "string" || typeof value === "number")) {
    return { kind: "choice", options: schema.enum as (string | number)[], labels: schema["x-labels"] };
  }
  if (type === "boolean") return { kind: "switch" };
  if (type === "string" && schema.format === TIMEZONE) return { kind: "timezone" };
  const options = schema["x-options"];
  if (type === "string" && options?.source && OPTION_SOURCES.includes(options.source)) {
    return {
      kind: "options",
      source: options.source,
      capability: typeof options.capability === "string" ? options.capability : undefined,
      none: typeof options.none === "string" ? options.none : undefined,
    };
  }
  if (type === "string" && schema.format === "duration") return { kind: "duration" };
  if (type === "string" && schema.format === "path") return { kind: "path" };
  if (type === "integer" || type === "number") {
    const options = steps(schema);
    if (options) return { kind: "choice", options, hours: schema["x-unit"] === HOUR };
    return {
      kind: "number",
      integer: type === "integer",
      min: schema.minimum ?? schema.exclusiveMinimum,
      max: schema.maximum ?? schema.exclusiveMaximum,
      exclusiveMin: schema.minimum === undefined && schema.exclusiveMinimum !== undefined,
      exclusiveMax: schema.maximum === undefined && schema.exclusiveMaximum !== undefined,
      unit: schema["x-unit"],
    };
  }
  if (type === "array") {
    const source = schema["x-options"]?.source;
    if (source && OPTION_SOURCES.includes(source)) {
      const capability = schema["x-options"]?.capability;
      return { kind: "multi", source, capability: typeof capability === "string" ? capability : undefined };
    }
    const pair = schema.prefixItems;
    if (pair?.length === 2) {
      const [first, second] = pair.map((item) => (item ? steps(item) : null));
      if (first && second && first.join() === second.join()) {
        return { kind: "range", options: first, hours: isHours(schema, first) };
      }
    }
    const item = unwrap(schema.items);
    if (item && typeOf(item) === "object" && item.properties && Object.keys(item.properties).length) {
      return { kind: "table", item, columns: Object.entries(item.properties) };
    }
    if (item && typeOf(item) === "string" && !item.enum) return { kind: "chips" };
  }
  if (type === "string") {
    return { kind: "text", input: TEXT_INPUTS[schema.format ?? ""] ?? "text", placeholder: placeholderOf(schema) };
  }
  return { kind: "text", input: "text" };
}

/** Whether a setting whose values are known (choices, a format, a type with a control of its own) still ended up as a
 *  field to type into: what docs/SETTINGS_UX.md says never to do. Only plain strings and opaque values may. */
export function typedThoughKnown(raw: JsonSchema | undefined | null): boolean {
  const schema = unwrap(raw ?? undefined);
  if (!schema || controlFor(schema).kind !== "text") return false;
  const type = typeOf(schema);
  return Boolean(
    schema.enum ||
      schema["x-options"] ||
      type === "boolean" ||
      type === "integer" ||
      type === "number" ||
      (schema.format && !(schema.format in TEXT_INPUTS)),
  );
}

/** Whether Settings lists a setting: never a deprecated one or one another control edits; expert ones in expert mode. */
export function listed(raw: JsonSchema | undefined | null, expert: boolean): boolean {
  if (raw?.deprecated || raw?.["x-hidden"]) return false;
  return expert || !raw?.["x-expert"];
}

/** A setting's label: its schema's title when it was given one, else its key in words ("API allowed hosts"). */
export function labelOf(key: string, raw?: JsonSchema | null): string {
  const automatic = key
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
  const title = raw?.title;
  return title && title !== automatic ? title : inWords(key);
}

/** `90m`, `36h`, `2d` (or `30s`) as a number and its unit; null for anything else. */
export function parseDuration(text: unknown): { amount: number; unit: "s" | "m" | "h" | "d" } | null {
  const match = typeof text === "string" ? /^(\d+)([smhd])$/.exec(text) : null;
  return match ? { amount: Number(match[1]), unit: match[2] as "s" | "m" | "h" | "d" } : null;
}

/** The browser's own time zone, for a new row. */
export const ownZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

/** A sensible new value of `schema`: its default; for a time zone the browser's; for bounds their lowest. */
export function initial(raw: JsonSchema): unknown {
  const schema = unwrap(raw) ?? raw;
  if (schema.default !== undefined && schema.default !== null) return structuredClone(schema.default);
  const control = controlFor(schema);
  switch (control.kind) {
    case "timezone":
      return ownZone();
    case "switch":
      return false;
    case "choice":
      return control.options[0];
    case "number":
      return control.min ?? 0;
    case "range":
      return [control.options[0], control.options.at(-1)];
    case "duration":
      return "1h";
    case "table":
    case "chips":
      return [];
    default:
      return typeOf(schema) === "object" && schema.properties
        ? Object.fromEntries(Object.entries(schema.properties).map(([key, value]) => [key, initial(value)]))
        : "";
  }
}

/** One row of a table as an object: a plain value (a zone given by its name) fills the first required column. */
export function asRow(value: unknown, item: JsonSchema): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  const first = item.required?.[0] ?? Object.keys(item.properties ?? {})[0] ?? "value";
  return { [first]: value };
}

const BARE = /^[A-Za-z0-9_-]+$/;

/** `value` as the TOML `hz system config set` reads: inline tables and arrays; JSON's strings are TOML's. */
export function toToml(value: unknown): string {
  if (value === null || value === undefined) return '""';
  // JSON's escapes are TOML's, except that TOML wants DEL escaped too.
  if (typeof value === "string") return JSON.stringify(value).replaceAll("\x7f", "\\u007f");
  if (typeof value === "number") {
    if (Number.isNaN(value)) return "nan";
    return Number.isFinite(value) ? String(value) : value > 0 ? "inf" : "-inf";
  }
  if (typeof value === "boolean") return String(value);
  // TOML has no null: a missing item is left out, as the server does.
  if (Array.isArray(value))
    return `[${value
      .filter((item) => item != null)
      .map(toToml)
      .join(", ")}]`;
  if (typeof value === "object") {
    const pairs = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== null && item !== undefined)
      .map(([key, item]) => `${BARE.test(key) ? key : JSON.stringify(key)} = ${toToml(item)}`);
    return pairs.length ? `{ ${pairs.join(", ")} }` : "{}";
  }
  return JSON.stringify(String(value));
}
