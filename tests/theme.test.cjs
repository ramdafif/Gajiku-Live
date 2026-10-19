const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/theme.js"), "utf8");
const storageKey = "gajiku-color-scheme";

const eventTarget = () => {
  const listeners = new Map();
  return {
    addEventListener(type, listener, options = {}) {
      const entries = listeners.get(type) || [];
      entries.push({ listener, once: options.once });
      listeners.set(type, entries);
    },
    emit(type, event = {}) {
      [...(listeners.get(type) || [])].forEach((entry) => {
        if (entry.once) {
          listeners.set(type, listeners.get(type).filter((item) => item !== entry));
        }
        entry.listener(event);
      });
    },
  };
};

const element = (initial = {}) => {
  const attributes = new Map(Object.entries(initial));
  return {
    hidden: true,
    setAttribute(name, value) { attributes.set(name, String(value)); },
    getAttribute(name) { return attributes.get(name) ?? null; },
    removeAttribute(name) { attributes.delete(name); },
    closest(selector) { return selector === "[data-theme-toggle]" ? this : null; },
  };
};

const createHarness = (options = {}) => {
  const storageData = options.storageData || new Map();
  if (options.stored !== undefined) storageData.set(storageKey, options.stored);
  const writes = [];
  const storage = {
    getItem(key) {
      if (options.blockedRead) throw new Error("Storage read blocked");
      return storageData.get(key) ?? null;
    },
    setItem(key, value) {
      if (options.blockedWrite) throw new Error("Storage write blocked");
      storageData.set(key, value);
      writes.push([key, value]);
    },
  };
  const system = { ...eventTarget(), matches: options.dark || false };
  const root = { dataset: {} };
  const buttons = Array.from({ length: options.buttonCount ?? 2 }, () => element());
  const metas = [
    element({ media: "(prefers-color-scheme: light)" }),
    element({ media: "(prefers-color-scheme: dark)" }),
  ];
  const document = {
    ...eventTarget(),
    documentElement: root,
    readyState: options.loading ? "loading" : "complete",
    querySelectorAll(selector) {
      if (selector === 'meta[name="theme-color"]') return metas;
      assert.equal(selector, "[data-theme-toggle]");
      return this.readyState === "loading" ? [] : buttons;
    },
  };
  const window = {
    ...eventTarget(),
    matchMedia(query) {
      assert.equal(query, "(prefers-color-scheme: dark)");
      return system;
    },
  };
  Object.defineProperty(window, "localStorage", {
    get() {
      if (options.blockedAccess) throw new Error("Storage access blocked");
      return storage;
    },
  });
  vm.runInNewContext(source, { window, document }, { filename: "theme.js" });
  return {
    root, buttons, metas, writes, storage, storageData, document,
    theme: () => root.dataset.theme,
    click(index = 0) {
      document.emit("click", { target: buttons[index] });
    },
    systemChange(dark) {
      system.matches = dark;
      system.emit("change", { matches: dark });
    },
    storageChange(newValue, key = storageKey, storageArea = storage) {
      window.emit("storage", { key, newValue, storageArea });
    },
    ready() {
      document.readyState = "interactive";
      document.emit("DOMContentLoaded");
    },
  };
};

for (const dark of [false, true]) {
  test(`new browser follows ${dark ? "dark" : "light"} device without persisting a default`, () => {
    const harness = createHarness({ dark });
    assert.equal(harness.theme(), dark ? "dark" : "light");
    assert.equal(harness.storageData.has(storageKey), false);
    assert.deepEqual(harness.writes, []);
  });

  test(`saved ${dark ? "light" : "dark"} overrides the opposite device preference`, () => {
    const stored = dark ? "light" : "dark";
    const harness = createHarness({ dark, stored });
    assert.equal(harness.theme(), stored);
    harness.systemChange(!dark);
    harness.systemChange(dark);
    assert.equal(harness.theme(), stored);
    assert.deepEqual(harness.writes, []);
  });
}

test("theme and browser toolbar are set before the body is parsed", () => {
  const harness = createHarness({ loading: true, stored: "dark" });
  assert.equal(harness.theme(), "dark");
  harness.metas.forEach((meta) => {
    assert.equal(meta.getAttribute("content"), "#17212b");
    assert.equal(meta.getAttribute("media"), null);
  });
  assert.equal(harness.buttons[0].hidden, true);
  harness.ready();
  harness.buttons.forEach((button) => {
    assert.equal(button.hidden, false);
    assert.equal(button.getAttribute("aria-label"), "Aktifkan mode terang");
    assert.equal(button.getAttribute("aria-pressed"), "true");
  });
});

test("toggle switches in both directions and synchronizes every control", () => {
  const harness = createHarness();
  harness.click();
  assert.equal(harness.theme(), "dark");
  harness.buttons.forEach((button) => {
    assert.equal(button.getAttribute("title"), "Aktifkan mode terang");
    assert.equal(button.getAttribute("aria-pressed"), "true");
  });
  harness.click(1);
  assert.equal(harness.theme(), "light");
  harness.buttons.forEach((button) => {
    assert.equal(button.getAttribute("aria-label"), "Aktifkan mode gelap");
    assert.equal(button.getAttribute("aria-pressed"), "false");
  });
  harness.metas.forEach((meta) => assert.equal(meta.getAttribute("content"), "#f6f8fb"));
  assert.deepEqual(harness.writes, [[storageKey, "dark"], [storageKey, "light"]]);
});

test("clicks on the nested SVG icon activate the toggle", () => {
  const harness = createHarness();
  harness.document.emit("click", { target: { closest: () => harness.buttons[0] } });
  assert.equal(harness.theme(), "dark");
});

test("unrelated clicks do not change the theme", () => {
  const harness = createHarness();
  harness.document.emit("click", { target: {} });
  harness.document.emit("click", { target: { closest: () => null } });
  assert.equal(harness.theme(), "light");
  assert.deepEqual(harness.writes, []);
});

test("automatic theme continues following device changes without saving them", () => {
  const harness = createHarness();
  harness.systemChange(true);
  assert.equal(harness.theme(), "dark");
  harness.systemChange(false);
  assert.equal(harness.theme(), "light");
  assert.deepEqual(harness.writes, []);
});

test("manual selection stops subsequent device changes from overriding it", () => {
  const harness = createHarness();
  harness.click();
  harness.systemChange(true);
  harness.systemChange(false);
  assert.equal(harness.theme(), "dark");
});

test("manual preference survives a new page and a changed device theme", () => {
  const storageData = new Map();
  createHarness({ storageData }).click();
  const nextPage = createHarness({ storageData });
  assert.equal(nextPage.theme(), "dark");
  nextPage.click();
  assert.equal(createHarness({ storageData, dark: true }).theme(), "light");
});

for (const stored of ["", "new", "legacy", "auto", "DARK", "null"]) {
  test(`invalid stored value ${JSON.stringify(stored)} falls back to the device`, () => {
    const harness = createHarness({ stored, dark: true });
    assert.equal(harness.theme(), "dark");
    harness.systemChange(false);
    assert.equal(harness.theme(), "light");
    assert.deepEqual(harness.writes, []);
  });
}

for (const failure of ["blockedAccess", "blockedRead", "blockedWrite"]) {
  test(`${failure} does not prevent in-memory toggling`, () => {
    const harness = createHarness({ [failure]: true, dark: true });
    assert.equal(harness.theme(), "dark");
    harness.click();
    assert.equal(harness.theme(), "light");
    harness.systemChange(false);
    harness.systemChange(true);
    assert.equal(harness.theme(), "light");
    harness.click();
    assert.equal(harness.theme(), "dark");
  });
}

test("storage events synchronize other tabs without writing back", () => {
  const harness = createHarness();
  harness.storageChange("dark");
  assert.equal(harness.theme(), "dark");
  harness.storageChange("light");
  assert.equal(harness.theme(), "light");
  harness.systemChange(true);
  assert.equal(harness.theme(), "light");
  assert.deepEqual(harness.writes, []);
});

for (const reset of [null, "invalid"]) {
  test(`removed or invalid cross-tab preference (${reset}) restores system mode`, () => {
    const harness = createHarness({ stored: "light", dark: true });
    harness.storageChange(reset);
    assert.equal(harness.theme(), "dark");
    harness.systemChange(false);
    assert.equal(harness.theme(), "light");
  });
}

test("clearing storage in another tab restores automatic mode", () => {
  const harness = createHarness({ stored: "dark" });
  harness.storageChange(null, null);
  assert.equal(harness.theme(), "light");
  harness.systemChange(true);
  assert.equal(harness.theme(), "dark");
});

test("unrelated keys and session storage events are ignored", () => {
  const harness = createHarness();
  harness.storageChange("dark", "another-key");
  harness.storageChange("dark", storageKey, {});
  assert.equal(harness.theme(), "light");
});

test("authentication pages work without theme controls", () => {
  const harness = createHarness({ buttonCount: 0, dark: true, loading: true });
  harness.ready();
  harness.systemChange(false);
  assert.equal(harness.theme(), "light");
});

test("the old layout theme key is not reused as a color preference", () => {
  const storageData = new Map([["gajiku-theme", "new"]]);
  assert.equal(createHarness({ storageData, dark: true }).theme(), "dark");
  assert.equal(storageData.get("gajiku-theme"), "new");
});
