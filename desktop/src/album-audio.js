const audio = document.querySelector('audio');
window.albumAudio = async (action, source) => {
  if (action === 'source') { audio.pause(); audio.src = source; audio.load(); }
  if (action === 'play') await audio.play();
  if (action === 'pause') audio.pause();
  if (action === 'volume') {
    if (typeof source !== 'number' || !Number.isFinite(source) || source < 0 || source > 1)
      throw new Error('Invalid album gain.');
    audio.volume = source;
  }
  return { playing: !audio.paused && !audio.ended, ended: audio.ended,
    gain: audio.muted ? 0 : audio.volume, positionSeconds: audio.currentTime };
};
