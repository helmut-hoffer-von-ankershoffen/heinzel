// Fixed catalog transport. The product page never supplies a URL or a filesystem path.
import { createHash, randomUUID } from "node:crypto";
import { readFile, mkdir, writeFile, rename, unlink } from "node:fs/promises";
import path from "node:path";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { Readable } from "node:stream";
import { fileURLToPath } from "node:url";

export const album = JSON.parse(await readFile(new URL("./album-catalog.json", import.meta.url))).albums[0];
export function albumIntent(action, options = {}) {
  if (action === "volume" && options && typeof options === "object" && !Array.isArray(options) &&
      Object.keys(options).length === 1 && Object.hasOwn(options, "gain") &&
      typeof options.gain === "number" && Number.isFinite(options.gain) && options.gain >= 0 && options.gain <= 1)
    return action;
  if (action === "seek" && options && typeof options === "object" && !Array.isArray(options) &&
      Object.keys(options).length === 2 && Object.hasOwn(options, "trackID") && Object.hasOwn(options, "seconds") &&
      album.tracks.some(track => track.id === options.trackID) && Number.isFinite(options.seconds) && options.seconds >= 0)
    return action;
  const actions = ["status", "catalog", "cover", "select", "play", "pause", "pausePlayback", "toggle", "next", "previous", "repeat", "download", "pauseDownload", "resumeDownload", "cancelDownload"];
  if (!actions.includes(action) || !options || typeof options !== "object" || Array.isArray(options) ||
      Object.keys(options).some(key => key !== "trackID") ||
      (action !== "select" && Object.keys(options).length) ||
      (action === "select" && !album.tracks.some(track => track.id === options.trackID))) throw new Error("Unknown album intent.");
  return action;
}
export function verified(bytes, media) {
  return bytes.length === media.bytes && createHash("sha256").update(bytes).digest("hex") === media.sha256;
}

export const filmDigest = "306c4b7f00c9f89aa80b624f7dcaeace22c52a14ece3b3f7a1e9b107d6c0f126";
export const filmURL = `heinzel-album://film/${filmDigest}.mp4`;
export function remoteURL(media) {
  if (!/^[a-f0-9]{64}$/.test(media.sha256) || media.key !== `/media/sha256/${media.sha256}.mp4`)
    throw new Error("Invalid catalog object.");
  return "https://latentheinzels.heinzel.team" + media.key;
}
const verifiedFiles = new Map();
const localURL = media => `heinzel-album://film/${media.sha256}.mp4`;
async function verifiedFile(root, media) {
  const filename = path.join(root, media.sha256);
  try {
    const info = await stat(filename);
    const identity = `${info.dev}:${info.ino}:${info.size}:${info.mtimeMs}:${info.ctimeMs}`;
    if (info.size !== media.bytes) return null;
    if (verifiedFiles.get(filename) !== identity) {
      const hash = createHash("sha256");
      for await (const chunk of createReadStream(filename)) hash.update(chunk);
      if (hash.digest("hex") !== media.sha256) return null;
      verifiedFiles.set(filename, identity);
    }
    return filename;
  } catch { return null; }
}
export async function filmResponse(request, root) {
  if (request.url !== filmURL) return new Response(null, { status: 404 });
  return catalogMediaResponse(request, root);
}
// Install before the workspace navigates so its document receives the custom URL loader.
export function installWorkspaceFilm(session, current, admitted, root) {
  session.webRequest.onBeforeRequest((details, callback) => {
    if (!details.url.startsWith("heinzel-album:")) return callback({ cancel: false });
    const contents = current();
    const allowed = contents && details.webContentsId === contents.id &&
      details.url === filmURL && details.resourceType === "media" &&
      ["GET", "HEAD"].includes(details.method) && admitted(contents, details.frame);
    callback({ cancel: !allowed });
  });
  session.protocol.handle("heinzel-album", request => {
    const contents = current();
    if (!contents || contents.isDestroyed() || !admitted(contents, contents.mainFrame))
      return new Response(null, { status: 403 });
    return filmResponse(request, root);
  });
}
async function catalogMediaResponse(request, root) {
  const media = album.tracks.find(t => localURL(t.media) === request.url)?.media;
  if (!media || !["GET", "HEAD"].includes(request.method)) return new Response(null, { status: 404 });
  const filename = await verifiedFile(root, media);
  if (!filename) return new Response(null, { status: 404 });
  const bytes = { length: media.bytes };
  const headers = { "Content-Type": "video/mp4", "Accept-Ranges": "bytes" };
  const range = request.headers.get("range");
  let start = 0, end = bytes.length - 1;
  if (range) {
    const match = /^bytes=(\d*)-(\d*)$/.exec(range);
    if (!match || (!match[1] && !match[2])) return new Response(null, { status: 416 });
    start = match[1] ? Number(match[1]) : Math.max(0, bytes.length - Number(match[2]));
    end = match[1] && match[2] ? Math.min(Number(match[2]), end) : end;
    if (start > end || start >= bytes.length) return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${bytes.length}` } });
    headers["Content-Range"] = `bytes ${start}-${end}/${bytes.length}`;
  }
  headers["Content-Length"] = String(end - start + 1);
  return new Response(request.method === "HEAD" ? null : Readable.toWeb(createReadStream(filename, { start, end })), { status: range ? 206 : 200, headers });
}

export function createAlbumTransport({ BrowserWindow, app, publish, fetchObject = fetch }) {
  let window, loaded, index = 0, repeatMode = "Off", playing = false, phase = "idle", error = null;
  let revision = 0, queue = Promise.resolve();
  let pendingBytes = 0, selected = false, sourced = false;
  let positionSeconds = 0, durationSeconds = null, admission = "remote", controller, downloadJob;
  let gain = 1;
  const observe = state => {
    playing = state.playing === true && state.ended !== true;
    positionSeconds = Number.isFinite(state.positionSeconds) ? state.positionSeconds : 0;
    durationSeconds = state.durationSeconds ?? null;
    gain = state.gain;
  };
  const saved = new Set();
  const snapshot = () => ({
    phase, verified: saved.size, total: album.tracks.length,
    verifiedBytes: album.tracks.filter(t => saved.has(t.id)).reduce((n, t) => n + t.media.bytes, 0),
    receivedBytes: pendingBytes + album.tracks.filter(t => saved.has(t.id)).reduce((n, t) => n + t.media.bytes, 0),
    totalBytes: album.tracks.reduce((n, t) => n + t.media.bytes, 0), error,
    positionSeconds, durationSeconds, playing, gain, title: album.tracks[index].title, repeatMode,
    media: { albumID: album.id, selectedTrackID: selected ? album.tracks[index].id : null,
      tracks: album.tracks.map(({ id, title }) => ({ id, title })),
      standardMediaID: selected ? album.tracks[index].media.sha256 : null, extendedMediaID: album.tracks[index].media.sha256 === filmDigest ? filmDigest : null,
      cover: null, still: null, extendedVideo: selected && album.tracks[index].media.sha256 === filmDigest ? {
        url: admission === "local" ? filmURL : remoteURL(album.tracks[index].media),
        sha256: filmDigest, trackID: album.tracks[index].id, admission,
      } : null, visualApproval: "approved" },
  });
  const emit = () => publish(snapshot());
  async function audio(action, source) {
    if ((!window || window.isDestroyed()) && action === "pause") return { positionSeconds: 0, playing: false, gain };
    if (!window || window.isDestroyed()) {
      window = new BrowserWindow({ show: false, webPreferences: {
        sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false,
        partition: `album-audio-${randomUUID()}`,
      } });
      const allowed = new Set(album.tracks.flatMap(track => [remoteURL(track.media), localURL(track.media)]));
      const contents = window.webContents;
      const documentURL = new URL("./album-audio.html", import.meta.url).href;
      const scriptURL = new URL("./album-audio.js", import.meta.url).href;
      // File documents can omit their referrer on custom-scheme media requests.
      contents.session.protocol.handle("heinzel-album", request => {
        if (contents.isDestroyed() || contents.getURL() !== documentURL)
          return new Response(null, { status: 403 });
        return catalogMediaResponse(request, root);
      });
      contents.session.webRequest.onBeforeRequest((details, callback) => {
        const fromAudioDocument = details.frame === contents.mainFrame && details.frame?.url === documentURL;
        const permitted = details.webContentsId === contents.id &&
          ((details.resourceType === "mainFrame" && details.url === documentURL) ||
           (fromAudioDocument && ((details.resourceType === "script" && details.url === scriptURL) ||
             (details.resourceType === "media" && allowed.has(details.url)))));
        callback({ cancel: !permitted });
      });
      window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
      window.webContents.on("will-navigate", event => event.preventDefault());
      loaded = window.loadFile(fileURLToPath(new URL("./album-audio.html", import.meta.url)));
      loaded = loaded.then(() => window.webContents.executeJavaScript(`window.albumAudio("volume",${JSON.stringify(gain)})`));
    }
    await loaded;
    return window.webContents.executeJavaScript(`window.albumAudio(${JSON.stringify(action)},${JSON.stringify(source ?? null)})`);
  }
  const root = path.join(app.getPath("userData"), "album-cache");
  async function cached(track) {
    const filename = await verifiedFile(root, track.media);
    if (filename) { saved.add(track.id); return filename; }
    saved.delete(track.id); return null;
  }
  async function download(track, signal) {
    if (await cached(track)) return;
    const media = track.media, url = remoteURL(media);
    const response = await fetchObject(url, { redirect: "error", signal });
    if (!response.ok || response.url !== url) throw new Error("Album download failed.");
    const chunks = []; let size = 0;
    for await (const chunk of response.body) {
      signal.throwIfAborted(); size += chunk.length;
      if (size > media.bytes) throw new Error("Album size mismatch.");
      chunks.push(chunk); pendingBytes = size; emit();
    }
    const bytes = Buffer.concat(chunks);
    if (!verified(bytes, media)) throw new Error("Album integrity mismatch.");
    signal.throwIfAborted();
    await mkdir(root, { recursive: true });
    const destination = path.join(root, media.sha256), temporary = destination + ".part";
    try {
      await writeFile(temporary, bytes, { mode: 0o600 });
      signal.throwIfAborted(); await rename(temporary, destination); saved.add(track.id);
    } finally { await unlink(temporary).catch(() => {}); }
    pendingBytes = 0; emit();
  }
  function startDownload(tracks) {
    if (downloadJob) return;
    controller = new AbortController(); const signal = controller.signal;
    phase = "downloading"; error = null; emit();
    downloadJob = (async () => {
      try {
        for (const track of tracks) await download(track, signal);
        signal.throwIfAborted();
        phase = saved.size === album.tracks.length ? "ready" : "idle";
      } catch (cause) {
        if (!signal.aborted) { phase = "failed"; error = cause.message; }
      } finally { pendingBytes = 0; downloadJob = null; controller = null; emit(); }
    })();
  }
  async function source() {
    const track = album.tracks[index], bytes = await cached(track);
    admission = bytes ? "local" : "remote"; positionSeconds = 0; durationSeconds = null;
    await audio("source", bytes ? localURL(track.media) : remoteURL(track.media));
    if (!bytes) startDownload([track]);
  }
  async function run(action, options) {
    if (window && !window.isDestroyed()) observe(await audio("status"));
    if (["status", "catalog", "cover"].includes(action)) return snapshot();
    if (action === "seek") {
      if (sourced && selected && options.trackID === album.tracks[index].id) {
        revision++;
        observe(await audio("seek", options.seconds));
        emit();
      }
      return snapshot();
    }
    error = null;
    if (action === "volume") observe(await audio("volume", options.gain));
    else if (["pauseDownload", "cancelDownload"].includes(action)) {
      phase = action === "pauseDownload" ? "paused" : "cancelled";
      controller?.abort(); await downloadJob;
    } else if (["download", "resumeDownload"].includes(action)) {
      controller?.abort(); await downloadJob; startDownload(album.tracks);
    } else if (action === "repeat") repeatMode = ({ Off: "Album", Album: "Track", Track: "Off" })[repeatMode];
    else if (["pause", "pausePlayback"].includes(action) || (action === "toggle" && playing)) {
      revision++; const state = await audio("pause");
      observe(state);
    } else if (!["status", "catalog"].includes(action)) {
      revision++;
      const wasPlaying = playing;
      const oldIndex = index;
      if (action === "select") index = album.tracks.findIndex(t => t.id === options.trackID);
      if (action === "next") index = (index + 1) % album.tracks.length;
      if (action === "previous") index = (index + album.tracks.length - 1) % album.tracks.length;
      selected = true;
      if (!sourced || oldIndex !== index || action === "next" || action === "previous") {
        await source(); sourced = true;
      }
      if (action !== "select" || wasPlaying) observe(await audio("play"));
      else observe(await audio("status"));

    }
    emit(); return snapshot();
  }
  const handle = (action, options = {}, admitted = () => true) => {
    albumIntent(action, options);
    if (!["status", "catalog"].includes(action)) revision++;
    const result = queue.then(() => {
      if (!admitted()) throw new Error("Album document changed.");
      return run(action, options);
    }).catch(cause => {
      playing = false; phase = "failed"; error = cause.message; emit(); throw cause;
    });
    queue = result.catch(() => {});
    return result;
  };
  const timer = setInterval(async () => {
    if (!playing) return;
    const version = revision;
    try {
      const state = await audio("status");
      if (version !== revision) return;
      observe(state); emit();
      if (state.ended) {
        playing = false;
        if (repeatMode === "Track") { sourced = false; void handle("play").catch(() => {}); }
        else if (index + 1 < album.tracks.length || repeatMode === "Album") void handle("next").catch(() => {});
        else emit();
      }
    } catch { playing = false; emit(); }
  }, 500);
  timer.unref();
  return { handle, snapshot };
}
