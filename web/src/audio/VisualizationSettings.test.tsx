import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { VisualizationSettings } from "./VisualizationSettings";
import { reloadSettings, setMaster } from "./viz/settings";

afterEach(() => {
  cleanup();
  localStorage.clear();
  reloadSettings();
});

it("says what is listened to and that every captured source waits for the person's own yes", () => {
  render(<VisualizationSettings />);
  const section = screen.getByRole("region", { name: "Audio visualisation" });
  expect(section).toHaveTextContent("Nothing is recorded or uploaded");
  const terms = screen.getAllByRole("term").map((term) => term.textContent);
  expect(terms).toEqual(["Heinzel's own sound", "Microphone or shared audio", "System audio"]);
  const [own, chosen, system] = screen.getAllByRole("definition").map((entry) => entry.textContent ?? "");
  expect(own).toMatch(/never leaves it/);
  expect(chosen).toMatch(/Only after you choose it .* your browser asks you/);
  expect(chosen).toMatch(/Choose Off there to stop/);
  expect(system).toMatch(/only when you press Use system audio; it asks every time/);
  // The consent stays readable with the visualisation switched off.
  act(() => setMaster(false));
  expect(screen.getAllByRole("term")).toHaveLength(3);
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
  // With the master switch off no style can be chosen, so none is shown, not a greyed-out list.
  act(() => setMaster(false));
  expect(screen.queryByRole("checkbox", { name: /^Spectrum/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox", { name: /^Aurora/ })).not.toBeInTheDocument();
  act(() => setMaster(true));
  expect(screen.getByRole("checkbox", { name: /^Aurora/ })).not.toBeChecked();
});
