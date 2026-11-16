"""Exercise the actual bounded WebKit script with fictional paired credentials."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_enrollment_script_restricts_credentials_origin_and_response_size():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to exercise the WebKit script")
    source = (ROOT / "apple/Shared/WidgetEnrollmentTransport.swift").read_text(encoding="utf-8")
    script = source.split('static let script = """', 1)[1].split('        """', 1)[0]
    harness = """
const assert = require('node:assert/strict');
const {webcrypto, createHash} = require('node:crypto');
globalThis.crypto = webcrypto;
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const origin = 'https://example.test';
const bearer = 'hzd_' + 'x'.repeat(43);
const fingerprint = createHash('sha256').update('heinzel-widget-pair-v1|' + origin + '|' + bearer).digest('hex');
const script = new AsyncFunction('origin','operation','payload','expectedFingerprint', SCRIPT);
const run = (origin, operation, payload) => script(origin, operation, payload, fingerprint);
globalThis.location = {origin};
globalThis.localStorage = {getItem: key => key === 'hz.device' ? bearer : null};
const epoch = 'a'.repeat(32), revision = 'b'.repeat(32), workspace = 'c'.repeat(64), attempt = 'd'.repeat(32);
const negotiated = {epoch, revision, workspace_id:workspace, last_attempt:null};
const receipt = {epoch, revision, workspace_id:workspace, attempt,
                 operation:'issue', expires_at:'2026-11-16T04:00:00Z'};
let status = 200, mime = 'application/json', calls = [];
let chunks = [Buffer.from(JSON.stringify({...negotiated, leaked:bearer}))];
let switchOrigin = false, switchPair = false, cancelled = false;
globalThis.fetch = async (url, opts) => {
  calls.push({url, opts});
  if (switchPair) globalThis.localStorage = {getItem: () => 'hzd_' + 'y'.repeat(43)};
  return {status, headers: {get: () => mime}, body: {getReader: () => {
    let index = 0;
    return {read: async () => {
      if (switchOrigin) globalThis.location.origin = 'https://other.test';
      return index < chunks.length ? {done:false, value:chunks[index++]} : {done:true};
    }, cancel: async () => { cancelled = true; }};
  }}};
};
(async () => {
  let result = await run(origin, 'negotiate', '');
  assert.deepEqual(result, {state:'ok', body:JSON.stringify(negotiated)});
  assert.equal(calls[0].url, '/api/_web/widget-fence');
  assert.equal(calls[0].opts.headers.Authorization, 'Bearer ' + bearer);
  assert.equal(calls[0].opts.credentials, 'omit');
  assert.equal(calls[0].opts.redirect, 'error');
  assert.equal(calls[0].opts.cache, 'no-store');
  assert(!JSON.stringify(result).includes(bearer));
  const payload = '{"commitment":"' + 'a'.repeat(64) + '"}';
  chunks = [Buffer.from(JSON.stringify({...receipt, leaked:bearer}))];
  result = await run(origin, 'mutate', payload);
  assert.deepEqual(result, {state:'ok', body:JSON.stringify(receipt)});
  assert(!JSON.stringify(result).includes(bearer));
  assert.equal(calls[1].url, '/api/_web/widget-fenced-credential');
  assert.equal(calls[1].opts.body, payload);
  assert(!calls[1].opts.body.includes(bearer));
  chunks = [Buffer.alloc(2048, 65), Buffer.alloc(2049, 65)];
  result = await run(origin, 'mutate', payload);
  assert.equal(result.state, 'unavailable'); assert(cancelled);
  chunks = [Buffer.from('{}')]; mime = 'text/html';
  assert.equal((await run(origin, 'negotiate', '')).state, 'unavailable');
  mime = 'application/json'; status = 409;
  assert.equal((await run(origin, 'mutate', payload)).state, 'conflict');
  status = 403;
  assert.equal((await run(origin, 'mutate', payload)).state, 'unauthorized');
  status = 200; switchOrigin = true;
  assert.equal((await run(origin, 'mutate', payload)).state, 'unavailable');
  globalThis.location.origin = origin; switchOrigin = false;
  globalThis.localStorage = {getItem: () => 'session-token'};
  const count = calls.length;
  assert.equal((await run(origin, 'mutate', payload)).state, 'unauthorized');
  assert.equal(calls.length, count);
  globalThis.localStorage = {getItem: () => 'hzd_' + 'y'.repeat(43)};
  assert.equal((await run(origin, 'mutate', payload)).state, 'unavailable');
  assert.equal(calls.length, count);
  let reads = 0;
  globalThis.localStorage = {getItem: () => ++reads === 1 ? bearer : 'hzd_' + 'y'.repeat(43)};
  assert.equal((await run(origin, 'mutate', payload)).state, 'unavailable');
  assert.equal(calls.length, count);
  globalThis.localStorage = {getItem: () => bearer}; switchPair = true;
  assert.equal((await run(origin, 'mutate', payload)).state, 'unavailable');
  assert.equal(calls.length, count + 1);
})().catch(error => { console.error(error); process.exit(1); });
""".replace("SCRIPT", json.dumps(script))
    subprocess.run([node, "-e", harness], check=True, timeout=15)
