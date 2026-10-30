// Production main with a synthetic local service and native consent always denied.
import assert from 'node:assert/strict';
import { constants } from 'node:fs';
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { delimiter, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron } from '../../web/node_modules/playwright-core/index.mjs';
import { withTimeout } from './harness-cleanup.mjs';
const dir = process.env.HZ_TEST_ELECTRON_DIR ?? fileURLToPath(new URL('../node_modules/electron/', import.meta.url));
if (!isAbsolute(dir) || JSON.parse(await readFile(join(dir, 'package.json'), 'utf8')).version !== '45.0.0') throw new Error('Electron 45 required');
const dist = join(dir, 'dist');
const executable = resolve(dist, (await readFile(join(dir, 'path.txt'), 'utf8')).trim());
if (relative(dist, executable).startsWith('..')) throw new Error('Invalid Electron executable');
await access(executable, constants.X_OK);
const root = await mkdtemp(join(tmpdir(), 'hz-consent-isolated-'));
for (const d of ['profile','session','logs','bin','home','tmp','config','state','data','cache']) await mkdir(join(root,d));
const token = 'synthetic_permission_test_token_1234567890';
const server = createServer((request, response) => {
  if (request.url === '/api/lifecycle') {
    response.writeHead(request.headers.authorization === `Bearer ${token}` ? 200 : 403, {'content-type': 'application/json'});
    response.end(JSON.stringify({instance:'isolated',version:'0.0.0'}));
  } else {
    response.writeHead(200, {'content-type':'text/html'});
    response.end('<button id="notification">Notification</button><button id="microphone">Microphone</button><script>notification.onclick=async()=>{document.title=await Notification.requestPermission()};microphone.onclick=async()=>{try{await navigator.mediaDevices.getUserMedia({audio:true});document.title="unexpected"}catch(e){document.title=e.name}}</script>');
  }
});
await new Promise((ok) => server.listen(0, '127.0.0.1', ok));
const cli = join(root,'bin','hz');
await writeFile(cli, `#!${process.execPath}\nconst fs=require('node:fs');\nfs.writeFileSync(process.env.HZ_NATIVE_TEST_ROOT+'/owned-pid', String(process.pid));\nprocess.stdout.write(process.env.HZ_TEST_URL+'\\n');\nprocess.on('SIGINT',()=>{fs.writeFileSync(process.env.HZ_NATIVE_TEST_ROOT+'/owned-exit','clean');process.exit(0)});\nsetInterval(()=>{},1000);\n`, {mode:0o700});
let instance, child, safe = true;
try {
  instance = await electron.launch({ executablePath: executable, args:[fileURLToPath(new URL('./native-consent-bootstrap.mjs', import.meta.url))], cwd:root, timeout:30000,
    env:{ PATH:`${join(root,'bin')}${delimiter}/usr/bin:/bin`, HOME:join(root,'home'), TMPDIR:join(root,'tmp'), USER:process.env.USER||'', LOGNAME:process.env.LOGNAME||'', LANG:'en_US.UTF-8', ...(process.env.DISPLAY ? {DISPLAY:process.env.DISPLAY} : {}), ...(process.env.XAUTHORITY ? {XAUTHORITY:process.env.XAUTHORITY} : {}), XDG_CONFIG_HOME:join(root,'config'), XDG_STATE_HOME:join(root,'state'), XDG_DATA_HOME:join(root,'data'), XDG_CACHE_HOME:join(root,'cache'), HZ_NATIVE_TEST_ROOT:root, HZ_TEST_URL:`http://127.0.0.1:${server.address().port}/#token=${token}` } });
  child = instance.process();
  const page = await instance.firstWindow({timeout:30000});
  await page.click('#notification');
  await page.waitForFunction(() => document.title === 'denied');
  await page.click('#microphone');
  await page.waitForFunction(() => document.title === 'NotAllowedError');
  assert.deepEqual(await instance.evaluate(() => globalThis.consentPrompts), ['Allow notifications?', 'Allow microphone?']);
  console.log('Production main requested separate native consent and denied both requests.');
} finally {
  if (instance) {
    try { await withTimeout(instance.close(), 10000, 'Electron did not close'); }
    catch { safe = false; if (child && child.exitCode === null && child.signalCode === null) child.kill('SIGTERM'); }
    if (child && child.exitCode === null && child.signalCode === null) safe = false;
  }
  if (instance) {
    try {
      const pid = Number(await readFile(join(root, 'owned-pid'), 'utf8'));
      if (!Number.isSafeInteger(pid) || pid <= 1 || await readFile(join(root, 'owned-exit'), 'utf8') !== 'clean') safe = false;
      else { try { process.kill(pid, 0); safe = false; } catch (error) { if (error.code !== 'ESRCH') safe = false; } }
    } catch { safe = false; }
  }
  await new Promise((ok) => server.close(ok));
  if (safe) await rm(root,{recursive:true,force:true});
  else throw new Error(`Shutdown uncertain; evidence retained at ${root}`);
}
