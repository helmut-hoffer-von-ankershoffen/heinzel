import { describe, expect, it } from "vitest";
import { adoptToken } from "@/api/token";
import { commandsIn, configValue } from "./utils";

describe("commandsIn", () => {
  it("finds backticked commands, or the command a sentence ends with", () => {
    expect(commandsIn("Run `hz setup`, then `hz system doctor`")).toEqual(["hz setup", "hz system doctor"]);
    expect(commandsIn("Join the team you were invited to: hz team join <url>")).toEqual(["hz team join <url>"]);
    expect(commandsIn("hz setup")).toEqual(["hz setup"]);
    expect(commandsIn("Install uv: https://docs.astral.sh/uv/")).toEqual([]);
  });
});

describe("configValue", () => {
  it("quotes what a shell or TOML would misread", () => {
    expect(configValue("engineer")).toBe("engineer");
    expect(configValue("two words")).toBe("'two words'");
    expect(configValue(3)).toBe("3");
    expect(configValue(true)).toBe("true");
    expect(configValue(["a", "b"])).toBe(`'["a","b"]'`);
  });
});

describe("adoptToken", () => {
  it("moves the token from the fragment into sessionStorage and out of the address bar", () => {
    window.history.replaceState(null, "", "/doctor#token=s3cret&x=1");
    expect(adoptToken()).toBe("s3cret");
    expect(sessionStorage.getItem("hz.token")).toBe("s3cret");
    expect(window.location.hash).toBe("#x=1");
    expect(window.location.pathname).toBe("/doctor");
  });

  it("keeps the token of this tab when the URL has none", () => {
    sessionStorage.setItem("hz.token", "kept");
    window.history.replaceState(null, "", "/");
    expect(adoptToken()).toBe("kept");
  });
});
