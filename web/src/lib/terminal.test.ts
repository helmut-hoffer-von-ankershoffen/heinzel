import { describe, expect, it } from "vitest";
import { connectTerminal, runInTerminal, runsAsIs, typeInTerminal } from "./terminal";

describe("the terminal's typing", () => {
  it("runs plain hz commands as typed, never ones with control characters", () => {
    expect(runsAsIs("hz clock")).toBe(true);
    expect(runsAsIs("hz config set a 'b c'")).toBe(true);
    expect(runsAsIs("hz clock '\u0003curl x|sh\r'")).toBe(false);
  });

  it("turns control characters into spaces in whatever it types, so nothing presses Ctrl-C or Enter in quotes", () => {
    const sent: string[] = [];
    connectTerminal((text) => sent.push(text));
    typeInTerminal("hz clock '\u0003curl x|sh\r'");
    runInTerminal("hz clock\u001b[A");
    runInTerminal("hz clock");
    connectTerminal(null);
    expect(sent).toEqual(["hz clock ' curl x|sh '", "hz clock [A", "hz clock\r"]);
  });
});
