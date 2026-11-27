import { expect, it } from "vitest";
import { clampToWindow } from "@/lib/placement";
import { floatingPlayerGeometry } from "./PlayerCard";

it("keeps the complete provider viewport within every narrow window", () => {
  for (let available = 200; available <= 500; available++) {
    const box = floatingPlayerGeometry(available);
    expect(box.width - 2 * box.border).toBeGreaterThanOrEqual(200);
    const at = clampToWindow(800, 800, { width: box.width, height: 244 }, { width: available, height: 400 });
    expect(at.x).toBeGreaterThanOrEqual(0);
    expect(at.x + box.width).toBeLessThanOrEqual(available);
    expect(at.y + 244).toBeLessThanOrEqual(400);
  }
});
