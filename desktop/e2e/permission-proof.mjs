// Synthetic page and denied native prompt only; never requests an OS grant.
import assert from 'node:assert/strict';
import { access, mkdir, mkdtemp, readFile, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron } from '../../web/node_modules/playwright-core/index.mjs';
import { withTimeout } from './harness-cleanup.mjs';

const directory = process.env.HZ_TEST_ELECTRON_DIR ?? fileURLToPath(new URL('../node_modules/electron/', import.meta.url));
if (!isAbsolute(directory)) throw new Error('Electron directory must be absolute');
const manifest = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'));
if (manifest.version !== '45.0.0') throw new Error('Electron 45.0.0 is required');
const dist = join(directory, 'dist');
const executable = resolve(dist, (await readFile(join(directory, 'path.txt'), 'utf8')).trim());
if (relative(dist, executable).startsWith('..')) throw new Error('Invalid Electron path');
await access(executable);
const root = await mkdtemp(join(tmpdir(), 'hz-permission-proof-'));
const server = createServer((_req, res) => { res.writeHead(200, {'content-type':'text/html'}); res.end('<button id="notification">Notification</button><button id="microphone">Microphone</button><script>notification.onclick=async()=>{document.title=await Notification.requestPermission()};microphone.onclick=async()=>{try{await navigator.mediaDevices.getUserMedia({audio:true});document.title="unexpected"}catch(e){document.title=e.name}}</script>'); });
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
let instance, child;
let safeToRemove = true;
try {
  for (const name of ['profile', 'config', 'state', 'cache', 'data']) await mkdir(join(root, name));
  instance = await electron.launch({executablePath: executable, args: [fileURLToPath(new URL('./permission-proof-bootstrap.mjs', import.meta.url))], cwd: root, timeout: 10000,
    env: {PATH: '/usr/bin:/bin', HOME: root, TMPDIR: root, USER: process.env.USER || '', LOGNAME: process.env.LOGNAME || '', LANG: 'en_US.UTF-8', ...(process.env.DISPLAY ? {DISPLAY: process.env.DISPLAY} : {}), ...(process.env.XAUTHORITY ? {XAUTHORITY: process.env.XAUTHORITY} : {}), XDG_CONFIG_HOME: join(root, 'config'), XDG_STATE_HOME: join(root, 'state'), XDG_CACHE_HOME: join(root, 'cache'), XDG_DATA_HOME: join(root, 'data'), HZ_TEST_PROFILE: join(root, 'profile'), HZ_TEST_ORIGIN: origin}});
  child = instance.process();
  const page = await instance.firstWindow({ timeout: 10000 });
  for (const id of ['notification', 'microphone']) {
    await page.click(`#${id}`, { timeout: 5000 });
    await page.waitForFunction(() => document.title !== '', null, {timeout: 10000});
    const events = await instance.evaluate(() => globalThis.permissionEvents);
    console.log(JSON.stringify({ id, title: await page.title(), events }));
    await page.evaluate(() => { document.title = ''; });
  }
  const events = await instance.evaluate(() => globalThis.permissionEvents);
  assert(events.some((e) => e.kind === 'request' && e.permission === 'notifications'));
  assert(events.some((e) => e.kind === 'request' && e.permission === 'media'));
  assert(events.filter((e) => e.kind === 'prompt').length === 2);
  const notice = events.find((e) => e.kind === 'request' && e.permission === 'notifications');
  const media = events.find((e) => e.kind === 'request' && e.permission === 'media');
  assert.equal(notice.details.isMainFrame, true);
  assert.equal(notice.details.requestingUrl, `${origin}/`);
  assert.equal(media.details.isMainFrame, true);
  assert.equal(media.details.requestingUrl, `${origin}/`);
  assert.deepEqual(media.details.mediaTypes, ['audio']);
  assert.equal(media.details.securityOrigin, `${origin}/`);
  // The accepted decision is injected directly into the adapter, not into an Electron request callback.
  // Querying permission status performs no OS permission request or notification display.
  assert.equal(await instance.evaluate(() => globalThis.authorizeSynthetic()), true);
  const state = await page.evaluate(async () => (await navigator.permissions.query({name:'notifications'})).state);
  const after = await instance.evaluate(() => globalThis.permissionEvents);
  console.log(JSON.stringify({ syntheticAcceptedStatus: state, checks: after.filter((e) => e.kind === 'check') }));
  assert.equal(state, 'granted');
  assert(after.some((e) => e.kind === 'check' && e.permission === 'notifications'));
  await page.reload();
  assert.equal(await page.evaluate(async () => (await navigator.permissions.query({name:'notifications'})).state), 'denied');
  assert((await instance.evaluate(() => globalThis.permissionEvents)).some((e) => e.kind === 'navigation' && e.isMainFrame === true));

} finally {
  if (instance) {
    try { await withTimeout(instance.close(), 10000, 'Electron did not close'); }
    catch { safeToRemove = false; if (child && child.exitCode === null && child.signalCode === null) child.kill('SIGTERM'); }
    if (child && child.exitCode === null && child.signalCode === null) safeToRemove = false;
  }
  await new Promise((resolve) => server.close(resolve));
  if (safeToRemove) await rm(root, {recursive: true, force: true});
  else throw new Error(`Electron shutdown uncertain; evidence retained at ${root}`);
}
