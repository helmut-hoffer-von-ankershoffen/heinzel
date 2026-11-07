/** Reject relative, credential-bearing and non-web links from calendar data. */
export function conferenceLink(value: string | null | undefined): string | null {
  if (
    !value ||
    value.length > 2048 ||
    /[\s\\"'<>]/.test(value) ||
    [...value].some((char) => {
      const code = char.codePointAt(0) ?? 0;
      return code < 32 || (code >= 127 && code <= 159);
    }) ||
    !/^https?:\/\//i.test(value)
  )
    return null;
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") &&
      url.hostname &&
      !url.username &&
      !url.password &&
      url.port !== "0"
      ? url.href
      : null;
  } catch {
    return null;
  }
}
