import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { siblingStack } from "../src/sibling-stack.js";

function windowMock() {
  const children = [];
  let destroyed = false;
  const window = Object.assign(new EventEmitter(), {
    isDestroyed: () => destroyed,
    contentView: {
      addChildView(view) {
        const index = children.indexOf(view);
        if (index >= 0) children.splice(index, 1);
        children.push(view);
      },
      removeChildView(view) {
        const index = children.indexOf(view);
        if (index >= 0) children.splice(index, 1);
      },
    },
    destroy() {
      destroyed = true;
      Object.defineProperty(window, "contentView", { get() { throw new Error("Destroyed"); } });
      window.emit("closed");
    },
  });
  return { window, children, stack: siblingStack(window) };
}

for (const order of [ ["browser", "media", "chrome"], ["chrome", "media", "browser"],
  ["media", "browser", "chrome"], ["browser", "chrome", "media"],
  ["chrome", "browser", "media"], ["media", "chrome", "browser"] ]) {
  test(`sibling order is independent of creation: ${order}`, () => {
    const { stack, children } = windowMock();
    for (const role of order) stack.attach(role, role);
    assert.deepEqual(children, ["browser", "media", "chrome"]);
    stack.attach("browser2", "browser");
    stack.attach("media2", "media");
    assert.deepEqual(children, ["browser", "browser2", "media", "media2", "chrome"]);
    stack.release("browser");
    stack.release("browser");
    stack.attach("browser", "browser");
    assert.deepEqual(children, ["browser2", "browser", "media", "media2", "chrome"]);
    stack.release("media");
    stack.attach("media", "media");
    assert.deepEqual(children, ["browser2", "browser", "media2", "media", "chrome"]);
  });
}

test("window isolation, duplicate attachment, validation and destroyed cleanup", () => {
  const a = windowMock(), b = windowMock();
  assert.equal(siblingStack(a.window), a.stack);
  a.stack.attach("view", "media");
  a.stack.attach("view", "media");
  assert.deepEqual(a.children, ["view"]);
  assert.deepEqual(b.children, []);
  assert.throws(() => a.stack.attach("view", "chrome"));
  assert.throws(() => a.stack.attach("other", "untrusted"));
  a.window.destroy();
  a.stack.release("view");
  assert.throws(() => a.stack.attach("new", "browser"));
  b.stack.attach("view", "browser");
  assert.deepEqual(b.children, ["view"]);
});
