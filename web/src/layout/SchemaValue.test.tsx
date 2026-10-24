import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SchemaValue } from "./SchemaValue";

const role = { type: "string", enum: ["engineering-manager", "engineer", "product-manager", "program-manager"] };

describe("A choice", () => {
  it("reads a value that is no longer offered, such as a retired role, as not set", () => {
    render(<SchemaValue label="Role" schema={role} value="principal" disabled={false} onChange={() => {}} />);
    const select = screen.getByRole("combobox", { name: "Role" });
    expect(select).toHaveValue("");
    expect(screen.getByRole("option", { name: "not set" })).toBeInTheDocument();
  });

  it("shows an offered value as chosen, without a not-set entry", () => {
    render(<SchemaValue label="Role" schema={role} value="engineer" disabled={false} onChange={() => {}} />);
    expect(screen.getByRole("combobox", { name: "Role" })).toHaveValue("engineer");
    expect(screen.queryByRole("option", { name: "not set" })).not.toBeInTheDocument();
  });
});
