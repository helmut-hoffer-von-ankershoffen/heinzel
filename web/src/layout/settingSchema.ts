// A setting's JSON Schema (`SettingInfo.json_schema`, pydantic's, with every $ref inlined) read for one purpose:
// which control edits it. And the TOML text `hz system config set` takes for a value the control produced.

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
  /** Where the valid values come from when the server knows them but they change with what is installed (ADR 0055). */
  "x-options"?: OptionsSource;
};

/** A source of valid values the web app resolves: a capability's adapters (with `none`), themes, layers, modules. */
export type OptionsSource = { source: string; capability?: string; none?: string };

export const TIMEZONE = "iana-timezone";
/** A range this small is a list of choices (the hours of a day are 25). */
const FEW = 48;

/** `X | None` is `anyOf: [X, {type: null}]`: the schema of X. A union of real types stays as it is. */
export function unwrap(schema: JsonSchema | undefined): JsonSchema | undefined {
  const branches = schema?.anyOf ?? schema?.oneOf;
  if (!schema || !branches) return schema;
  const real = branches.filter((branch) => branch.type !== "null");
  if (real.length !== 1) return schema;
  return { ...real[0], description: schema.description ?? real[0]?.description, default: schema.default };
}

const typeOf = (schema: JsonSchema) => (Array.isArray(schema.type) ? undefined : schema.type);

export type Control =
  | { kind: "switch" }
  | { kind: "choice"; options: (string | number)[] }
  | { kind: "number"; integer: boolean; min?: number; max?: number }
  | { kind: "timezone" }
  | { kind: "range"; options: number[] }
  | { kind: "chips" }
  | { kind: "table"; item: JsonSchema; columns: [string, JsonSchema][] }
  | { kind: "options"; from: OptionsSource; multiple: boolean }
  | { kind: "text" };

/** The whole numbers from `schema`'s bounds, when there are few enough to choose from. */
function steps(schema: JsonSchema): number[] | null {
  if (typeOf(schema) !== "integer") return null;
  const min = schema.minimum ?? (schema.exclusiveMinimum !== undefined ? schema.exclusiveMinimum + 1 : undefined);
  const max = schema.maximum ?? (schema.exclusiveMaximum !== undefined ? schema.exclusiveMaximum - 1 : undefined);
  if (min === undefined || max === undefined || max < min || max - min > FEW) return null;
  return Array.from({ length: max - min + 1 }, (_, index) => min + index);
}

/** The control for a value of `raw`, or text when the schema says too little. */
export function controlFor(raw: JsonSchema | undefined | null): Control {
  const schema = unwrap(raw ?? undefined);
  if (!schema) return { kind: "text" };
  const type = typeOf(schema);
  // Known values that depend on what is installed: a choice the app fills in, never a text field (ADR 0055).
  const from = raw?.["x-options"] ?? schema["x-options"];
  if (from) return { kind: "options", from, multiple: type === "array" };
  if (schema.enum?.every((value) => typeof value === "string" || typeof value === "number")) {
    return { kind: "choice", options: schema.enum as (string | number)[] };
  }
  if (type === "boolean") return { kind: "switch" };
  if (type === "string" && schema.format === TIMEZONE) return { kind: "timezone" };
  if (type === "integer" || type === "number") {
    const options = steps(schema);
    if (options) return { kind: "choice", options };
    return { kind: "number", integer: type === "integer", min: schema.minimum, max: schema.maximum };
  }
  if (type === "array") {
    const pair = schema.prefixItems;
    if (pair?.length === 2) {
      const [first, second] = pair.map((item) => (item ? steps(item) : null));
      if (first && second && first.join() === second.join()) return { kind: "range", options: first };
    }
    const item = unwrap(schema.items);
    if (item && typeOf(item) === "object" && item.properties && Object.keys(item.properties).length) {
      return { kind: "table", item, columns: Object.entries(item.properties) };
    }
    if (item && typeOf(item) === "string" && !item.format && !item.enum) return { kind: "chips" };
  }
  return { kind: "text" };
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
