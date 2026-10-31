import assert from 'node:assert/strict';
import test from 'node:test';
import { diagnosePermissionClick } from '../e2e/permission-diagnostics.mjs';

test('diagnostic failure retains the exact original click error', async () => {
  const original = new Error('original click');
  const prior = console.error;
  const logs = [];
  console.error = (...values) => logs.push(values.join(' '));
  try {
    await assert.rejects(diagnosePermissionClick({ evaluate: async () => { throw new Error('private data'); } }, {}, 'notification', original),
      (error) => error === original);
    assert.deepEqual(logs, ['Permission click diagnostics unavailable']);
  } finally { console.error = prior; }
});

test('successful observation reports fixed values and preserves click failure', async () => {
  const original = new Error('original click');
  const prior = console.error;
  const logs = [];
  console.error = (...values) => logs.push(values.join(' '));
  try {
    const page = { evaluate: async () => ({documentVisible:false,exists:true,enabled:true,display:true,visibility:true,hasSize:true,framesAtLeastTwo:false}) };
    const instance = { evaluate: async () => false };
    await assert.rejects(diagnosePermissionClick(page, instance, 'notification', original), (error) => error === original);
    assert.match(logs[0], /"framesAtLeastTwo":false/);
    assert.match(logs[0], /"windowVisible":false/);
  } finally { console.error = prior; }
});

test('extra properties are never logged and malformed values fail closed', async () => {
  const original = new Error('original click');
  const prior = console.error;
  const logs = [];
  console.error = (...values) => logs.push(values.join(' '));
  const good = {documentVisible:true,exists:true,enabled:true,display:true,visibility:true,hasSize:true,framesAtLeastTwo:true,secret:'private-marker'};
  try {
    await assert.rejects(diagnosePermissionClick({ evaluate: async () => good }, { evaluate: async () => true }, 'notification', original), (error) => error === original);
    assert(!logs[0].includes('private-marker'));
    for (const [result, window] of [[{ ...good, enabled:'private-marker' }, true], [good, 'private-marker'], [null, true]]) {
      await assert.rejects(diagnosePermissionClick({ evaluate: async () => result }, { evaluate: async () => window }, 'notification', original), (error) => error === original);
      assert.equal(logs.at(-1), 'Permission click diagnostics unavailable');
    }
  } finally { console.error = prior; }
});
