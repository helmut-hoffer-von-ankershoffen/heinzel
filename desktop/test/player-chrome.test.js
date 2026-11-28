import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { chromeCommand, createPlayerChrome } from '../src/player-chrome.js';

test('chrome commands exclude navigation, scripts, surplus fields and invalid geometry', () => {
  for (const request of [null, { action: 'navigate', url: 'https://example.com' }, { action: 'play', script: 'x' }, { action: 'volume', value: 101 }, { action: 'resize', value: NaN }, { action: 'move', x: Infinity, y: 0 }]) assert.throws(() => chromeCommand(request));
  for (const request of [{ action: 'play' }, { action: 'pause' }, { action: 'close' }, { action: 'redock' }, { action: 'volume', value: 0 }, { action: 'resize', value: 482 }, { action: 'move', x: -16, y: 0 }]) assert.deepEqual(chromeCommand(request), request);
});

test('only the visible local main frame dispatches; suppression, blur and close revoke controls', async () => {
  const contents = Object.assign(new EventEmitter(), {
    mainFrame: {}, setWindowOpenHandler() {}, isDestroyed: () => false, close() {},
    async loadURL(url) { this.url = url; }, getURL() { return this.url; },
  });
  const view = { webContents: contents, setVisible(value) { this.visible = value; }, setBounds(value) { this.bounds = value; } };
  const owner = new EventEmitter();
  const window = Object.assign(new EventEmitter(), { webContents: owner, isDestroyed: () => false, isFocused: () => true, getContentSize: () => [1000, 800], contentView: { addChildView() {}, removeChildView() {} } });
  const calls = [];
  const chrome = await createPlayerChrome({ window, createView: () => view, session: {}, dispatch: (request) => calls.push(request) });
  const event = { sender: contents, senderFrame: contents.mainFrame };
  assert.throws(() => chrome.handle(event, { action: 'play' }));
  const bounds = { x: 10, y: 10, width: 480, height: 100 };
  const conditions = { suppressed: false, playerReady: true, dispatcherReady: true };
  assert.equal(chrome.layout(bounds, conditions), true);
  assert.throws(() => chrome.handle({ ...event, senderFrame: {} }, { action: 'play' }));
  chrome.handle(event, { action: 'play' });
  assert.deepEqual(calls, [{ action: 'play' }]);
  chrome.layout(bounds, { ...conditions, suppressed: true });
  assert.equal(view.visible, false);
  assert.throws(() => chrome.handle(event, { action: 'pause' }));
  chrome.layout(bounds, conditions);
  window.emit('blur');
  assert.throws(() => chrome.handle(event, { action: 'pause' }));
  chrome.close();
  assert.equal(chrome.layout(bounds, conditions), false);
});
