/** Every opening tag of `name` in `source`, up to its closing `>` outside braces (so `=>` in props is skipped). */
export function openingTags(source: string, name: string): string[] {
  const tags: string[] = [];
  const start = new RegExp(`<${name}(?=[\\s>])`, "g");
  for (let match = start.exec(source); match; match = start.exec(source)) {
    let depth = 0;
    let end = match.index;
    for (; end < source.length; end++) {
      const char = source[end];
      if (char === "{") depth++;
      else if (char === "}") depth--;
      else if (char === ">" && depth === 0 && source[end - 1] !== "=") break;
    }
    tags.push(source.slice(match.index, end + 1));
  }
  return tags;
}

/** Every opening tag, of any name, that carries `attribute` (e.g. `role="link"`). */
export function tagsWithAttribute(source: string, attribute: string): string[] {
  const names = new Set([...source.matchAll(/<([A-Za-z][\w.]*)(?=[\s>])/g)].map((match) => match[1] ?? ""));
  return [...names].flatMap((name) => openingTags(source, name)).filter((tag) => tag.includes(attribute));
}
