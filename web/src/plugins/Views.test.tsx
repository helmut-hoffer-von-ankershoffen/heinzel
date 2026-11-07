import { render, screen } from "@testing-library/react";
import { forwardRef, memo } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { confirmOperation, dismissOperation, getRun, resetOperationRun } from "@/lib/operationRun";
import { formCall, loadView, type ViewEntry, viewComponent } from "./Views";

const props = {
  data: 42,
  loading: false,
  visible: true,
  onClose: () => undefined,
  flowing: false,
  compact: false,
  run: () => undefined,
  submit: async () => undefined,
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

const report = {
  kind: "operation" as const,
  operation: "submit",
  arguments: {},
  inputs: ["text"],
  call: {
    method: "POST" as const,
    path: "/issues/submit",
    query: {},
    body: {},
    command: "hz issues submit",
    summary: "Report a problem or request something.",
    read_only: false,
  },
};

describe("a view's form", () => {
  afterEach(() => resetOperationRun());
  it("sends only declared inputs of a private mutation, shown as they will run", () => {
    const call = formCall(report, { text: "It's broken" });
    expect(call.body).toEqual({ text: "It's broken" });
    expect(call.command).toBe(`hz issues submit --text 'It'"'"'s broken'`);
    expect(() => formCall(report, { other: "x" })).toThrow("This form takes no other");
    expect(() => formCall({ ...report, call: { ...report.call, body: null } }, { text: "x" })).toThrow();
    expect(() => formCall({ kind: "navigate", path: "/" }, { text: "x" })).toThrow();
    expect(() => formCall(undefined, {})).toThrow();
  });
  it("asks first and fails when the person cancels", async () => {
    const pending = confirmOperation(formCall(report, { text: "x" }));
    expect(getRun().stage).toBe("confirm");
    dismissOperation();
    await expect(pending).rejects.toThrow("Cancelled");
  });
});
