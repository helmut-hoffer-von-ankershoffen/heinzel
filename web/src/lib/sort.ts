/** UTF-16 code-unit order: the order of a bare `.sort()`, spelled out so it never depends on the locale. */
export function compareCodeUnits(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** The keys of an object in code-unit order, for comparing shapes and digests that must not vary by machine. */
export function sortedKeys(value: object): string[] {
  return Object.keys(value).sort(compareCodeUnits);
}
