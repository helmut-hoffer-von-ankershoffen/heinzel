import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { AlbumProgress } from "./AlbumProgress";

const ready = { phase: "ready" as const, verified: 75, total: 75, verifiedBytes: 1000, totalBytes: 1000 };
const position = { elapsedSeconds: 62, durationSeconds: 184 };

it("shows download under the track-time rail, then removes only completed transfer", () => {
  const onSeek = vi.fn();
  const onTransfer = vi.fn();
  const view = render(
    <AlbumProgress
      transfer={{ ...ready, phase: "downloading", verified: 20, verifiedBytes: 400 }}
      position={position}
      onSeek={onSeek}
      onTransfer={onTransfer}
    />,
  );
  expect(screen.getByRole("progressbar", { name: "Album download" })).toHaveAttribute(
    "aria-valuetext",
    "400 of 1000 bytes",
  );
  expect(screen.getByText("Downloading album · 40%")).toBeInTheDocument();
  const slider = screen.getByRole("slider", { name: "Seek in track" });
  const rail = slider.closest(".album-seek-rail");
  expect(rail?.querySelector(".album-seek-track .album-seek-download")).toBeInTheDocument();
  expect(slider).toHaveAttribute("aria-valuetext", "1:02 of 3:04");
  fireEvent.click(screen.getByRole("button", { name: "Pause download" }));
  expect(onTransfer).toHaveBeenCalledWith("pauseDownload");
  expect(onSeek).not.toHaveBeenCalled();
  fireEvent.change(slider, { target: { value: "90" } });
  expect(onSeek).toHaveBeenCalledExactlyOnceWith(90);
  view.rerender(<AlbumProgress transfer={ready} position={position} onSeek={onSeek} onTransfer={onTransfer} />);
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  expect(screen.queryByText(/75\/75/)).not.toBeInTheDocument();
  expect(rail?.querySelector(".album-seek-download")).toBeNull();
  expect(slider).toHaveAttribute("aria-valuetext", "1:02 of 3:04");
});

it("commits a pointer scrub once on release", () => {
  const onSeek = vi.fn();
  render(<AlbumProgress transfer={ready} position={position} onSeek={onSeek} onTransfer={vi.fn()} />);
  const slider = screen.getByRole("slider", { name: "Seek in track" });
  fireEvent.pointerDown(slider);
  fireEvent.change(slider, { target: { value: "90" } });
  fireEvent.change(slider, { target: { value: "100" } });
  expect(onSeek).not.toHaveBeenCalled();
  fireEvent.pointerUp(slider);
  expect(onSeek).toHaveBeenCalledExactlyOnceWith(100);
});
