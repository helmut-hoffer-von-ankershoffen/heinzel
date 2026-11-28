/** The hidden album audio element owns the timeline, including seeks while paused. */
export function albumAudioPosition(audio) {
  return {
    positionSeconds: Number.isFinite(audio.currentTime) ? Math.max(0, audio.currentTime) : 0,
    durationSeconds: Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : null,
  };
}

export function seekAlbumAudio(audio, seconds) {
  const { durationSeconds } = albumAudioPosition(audio);
  if (durationSeconds === null || !Number.isFinite(seconds) || seconds < 0) {
    throw new Error("Track position unavailable.");
  }
  audio.currentTime = Math.min(seconds, durationSeconds);
  return albumAudioPosition(audio);
}
