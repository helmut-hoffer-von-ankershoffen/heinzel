import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ICONS } from "./Contributions";
import { HeinzelFace } from "./HeinzelFace";

describe("HeinzelFace", () => {
  it("is a one-colour line icon like the others, hidden from assistive tech", () => {
    const { container } = render(<HeinzelFace className="size-5" />);
    const svg = container.querySelector("svg") as SVGSVGElement;
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("stroke", "currentColor");
    expect(svg).toHaveAttribute("fill", "none");
    expect(svg).toHaveAttribute("stroke-width", "2");
    expect(svg).toHaveClass("size-5");
  });

  it("stands for the robot wherever a contribution names `bot`", () => {
    expect(ICONS.bot).toBe(HeinzelFace);
  });
});
