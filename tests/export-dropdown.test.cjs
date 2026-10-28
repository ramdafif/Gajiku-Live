const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");

const eventTarget = () => {
  const listeners = new Map();
  return {
    addEventListener(type, listener) {
      const entries = listeners.get(type) || [];
      entries.push(listener);
      listeners.set(type, entries);
    },
    emit(type, event) {
      for (const listener of listeners.get(type) || []) listener(event);
    },
  };
};

const setup = () => {
  let focused = null;
  const trigger = { focus() { focused = trigger; } };
  const option = {};
  const dropdown = {
    ...eventTarget(),
    open: true,
    querySelector: () => trigger,
    contains: (target) => [dropdown, trigger, option].includes(target),
  };
  const document = {
    ...eventTarget(),
    querySelector: () => null,
    querySelectorAll: (selector) => selector === "[data-export-dropdown]" ? [dropdown] : [],
    getElementById: () => null,
    body: { classList: { contains: () => false } },
  };
  const window = {
    ...eventTarget(),
    matchMedia: () => ({ ...eventTarget(), matches: false }),
  };
  vm.runInNewContext(source, { document, window });
  return { document, dropdown, trigger, option, focused: () => focused };
};

test("clicking inside keeps export options open; clicking outside closes them", () => {
  const state = setup();
  state.document.emit("click", { target: state.option });
  assert.equal(state.dropdown.open, true);
  state.document.emit("click", { target: {} });
  assert.equal(state.dropdown.open, false);
  assert.equal(state.focused(), null);
});

test("tabbing outside closes the dropdown without stealing focus", () => {
  const state = setup();
  state.document.emit("focusin", { target: state.option });
  assert.equal(state.dropdown.open, true);
  state.document.emit("focusin", { target: {} });
  assert.equal(state.dropdown.open, false);
  assert.equal(state.focused(), null);
});

test("Escape closes the dropdown and returns focus to its trigger", () => {
  const state = setup();
  let prevented = false;
  state.dropdown.emit("keydown", { key: "Tab" });
  assert.equal(state.dropdown.open, true);
  state.dropdown.emit("keydown", { key: "Escape", preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(state.dropdown.open, false);
  assert.equal(state.focused(), state.trigger);
});

test("export submission closes the dropdown without cancelling the download", () => {
  const state = setup();
  const event = { defaultPrevented: false };
  state.dropdown.emit("submit", event);
  assert.equal(event.defaultPrevented, false);
  assert.equal(state.dropdown.open, false);
  assert.equal(state.focused(), state.trigger);
});

test("cancelled submission leaves export options available", () => {
  const state = setup();
  state.dropdown.emit("submit", { defaultPrevented: true });
  assert.equal(state.dropdown.open, true);
  assert.equal(state.focused(), null);
});
