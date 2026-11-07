import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { VisualizationSettings } from "./VisualizationSettings";
import { reloadSettings, setMaster } from "./viz/settings";

afterEach(() => {
  cleanup();
  localStorage.clear();
  reloadSettings();
});

it("shows every style on by default, applies a change at once and keeps it", () => {
  render(<VisualizationSettings />);
  const spectrum = screen.getByRole("checkbox", { name: /^Spectrum/ });
  const aurora = screen.getByRole("checkbox", { name: /^Aurora/ });
  expect(spectrum).toBeChecked();
  expect(aurora).toBeChecked();
  fireEvent.click(aurora);
  expect(aurora).not.toBeChecked();
  expect(JSON.parse(localStorage.getItem("hz.audio.viz") ?? "{}").styles.aurora).toBe(false);
  act(() => setMaster(false));
  expect(spectrum).toBeDisabled();
});
