import { afterEach, describe, expect, it } from "vitest";
import { connectTerminal, offerInTerminal, runInTerminal, runsAsIs } from "./terminal";

afterEach(() => connectTerminal(null));

const typed = () => {
  const sent: string[] = [];
  connectTerminal((text) => sent.push(text));
  return sent;
};

describe("the terminal", () => {
  it("runs hz commands as they stand and leaves anything else for the person to run", () => {
    expect(runsAsIs("hz setup github")).toBe(true);
    expect(runsAsIs("gh auth login --hostname github.com")).toBe(false);
    const sent = typed();
    runInTerminal("hz setup github");
    runInTerminal("gh auth login --hostname github.com");
    expect(sent).toEqual(["hz setup github\r", "gh auth login --hostname github.com"]);
  });

  it("offers a connector's sign-in without Enter, also an hz one, and drops line breaks and escapes", () => {
    const sent = typed();
    offerInTerminal("acli jira auth login");
    offerInTerminal("hz system plugin add git+https://example.invalid/x --confirm");
    offerInTerminal("gh auth login\nrm -rf ~\u001b[2J");
    expect(sent).toEqual([
      "acli jira auth login",
      "hz system plugin add git+https://example.invalid/x --confirm",
      "gh auth login rm -rf ~ [2J",
    ]);
    expect(sent.some((text) => /[\r\n]/.test(text))).toBe(false);
  });
});
