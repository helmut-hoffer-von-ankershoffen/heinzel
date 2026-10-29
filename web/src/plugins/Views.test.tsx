import { render, screen } from "@testing-library/react";
import { forwardRef, memo } from "react";
import { describe, expect, it } from "vitest";
import { loadView, type ViewEntry, viewComponent } from "./Views";

const props = {
  data: 42,
  loading: false,
  visible: true,
  onClose: () => undefined,
  flowing: false,
  compact: false,
  run: () => undefined,
};

const Plain = () => <p>Independent view</p>;

describe("installed view SDK", () => {
  it.each(["plain", "memo", "forwardRef"])("accepts a valid %s React component", (kind) => {
    const source =
      kind === "memo"
        ? memo(Plain)
        : kind === "forwardRef"
          ? forwardRef<HTMLParagraphElement>(() => <p>Independent view</p>)
          : Plain;
    const Component = viewComponent(() => source);
    render(<Component {...props} />);
    expect(screen.getByText("Independent view")).toBeInTheDocument();
  });
  it("rejects factories that cannot supply a React element type", () => {
    expect(() => viewComponent(null)).toThrow("factory is missing");
    expect(() => viewComponent(() => ({}))).toThrow("returned no component");
  });
  it("refuses incompatible ABI and remote code before any module can execute", async () => {
    const entry = {
      abi: 999,
      bundle: "/plugin-assets/arbitrary/view/digest/index.js",
      fallback: "Use the CLI.",
      problem: null,
    } as ViewEntry;
    await expect(loadView(entry)).rejects.toThrow("Use the CLI");
    await expect(loadView({ ...entry, abi: 1, bundle: "https://outside.example/view.js" })).rejects.toThrow(
      "installed local asset",
    );
  });
});
