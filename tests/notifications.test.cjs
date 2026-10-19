const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/notifications.js"), "utf8");

class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.hidden = false;
    this.textContent = "";
    this.attributes = {};
  }
  append(child) { this.children.push(child); }
  replaceChildren(fragment) { this.children = [...fragment.children]; }
  setAttribute(name, value) { this.attributes[name] = value; }
  get childElementCount() { return this.children.length; }
}

const setup = ({ staff = true, dashboard = true, fetcher } = {}) => {
  const count = new Element();
  const bell = new Element("a");
  const banners = new Element("section");
  const listeners = new Map();
  const intervals = [];
  const requests = [];
  bell.dataset = { summaryUrl: "/api/notifications/summary" };
  bell.querySelector = () => count;
  const document = {
    hidden: false,
    querySelector: (selector) => selector === "[data-notification-bell]" ? (staff ? bell : null) : (dashboard ? banners : null),
    createElement: (tag) => new Element(tag),
    createDocumentFragment: () => new Element("fragment"),
    addEventListener: (name, listener) => listeners.set(name, listener),
  };
  const context = {
    document, URL, AbortController,
    window: { location: { origin: "https://fixture.invalid" } },
    setInterval: (callback, delay) => intervals.push({ callback, delay }),
    setTimeout: () => 1,
    clearTimeout: () => {},
    fetch: async (...args) => {
      requests.push(args);
      return fetcher ? fetcher(...args) : response({ unread_count: 3, alerts: [] });
    },
  };
  vm.runInNewContext(source, context);
  return { count, bell, banners, document, listeners, intervals, requests };
};

const response = (data, { ok = true, type = "application/json" } = {}) => ({
  ok,
  headers: { get: () => type },
  json: async () => data,
});

test("non-management pages do not start polling", () => {
  const state = setup({ staff: false });
  assert.equal(state.intervals.length, 0);
  assert.equal(state.requests.length, 0);
});

test("polling uses 60 seconds, same-origin credentials and a fresh count", async () => {
  const state = setup();
  assert.equal(state.intervals[0].delay, 60000);
  await state.intervals[0].callback();
  assert.equal(state.requests[0][0], "/api/notifications/summary");
  assert.equal(state.requests[0][1].credentials, "same-origin");
  assert.equal(state.requests[0][1].cache, "no-store");
  assert.equal(state.count.textContent, "3");
  assert.equal(state.count.hidden, false);
  assert.equal(state.bell.attributes["aria-label"], "Notifikasi, 3 belum dibaca");
});

test("zero unread hides the badge and empty alerts hide the banner", async () => {
  const state = setup({ fetcher: async () => response({ unread_count: 0, alerts: [] }) });
  await state.intervals[0].callback();
  assert.equal(state.count.hidden, true);
  assert.equal(state.banners.hidden, true);
});

test("large counts show 99+ while the accessible label retains the exact count", async () => {
  const state = setup({ fetcher: async () => response({ unread_count: 123, alerts: [] }) });
  await state.intervals[0].callback();
  assert.equal(state.count.textContent, "99+");
  assert.match(state.bell.attributes["aria-label"], /123/);
});

test("hidden tabs pause polling and returning to the tab refreshes", async () => {
  const state = setup();
  state.document.hidden = true;
  await state.intervals[0].callback();
  assert.equal(state.requests.length, 0);
  state.document.hidden = false;
  state.listeners.get("visibilitychange")();
  await new Promise(setImmediate);
  assert.equal(state.requests.length, 1);
});

test("overlapping refresh requests are prevented", async () => {
  let release;
  const state = setup({ fetcher: () => new Promise((resolve) => { release = resolve; }) });
  const first = state.intervals[0].callback();
  await state.intervals[0].callback();
  assert.equal(state.requests.length, 1);
  release(response({ unread_count: 1, alerts: [] }));
  await first;
  assert.equal(state.count.textContent, "1");
});

test("banner messages render as text, never executable HTML", async () => {
  const message = '<img src=x onerror="alert(1)">';
  const state = setup({ fetcher: async () => response({ unread_count: 1, alerts: [
    { kind: "danger", message, url: "/superadmin/clients/1#penagihan" },
  ] }) });
  await state.intervals[0].callback();
  const banner = state.banners.children[0];
  assert.equal(banner.className, "company-alert company-alert-danger");
  assert.equal(banner.children[0].textContent, message);
  assert.equal(banner.href, "/superadmin/clients/1#penagihan");
  assert.equal(state.banners.hidden, false);
});

test("cross-origin and unknown-severity banner links are ignored", async () => {
  const state = setup({ fetcher: async () => response({ unread_count: 1, alerts: [
    { kind: "danger", message: "Unsafe", url: "https://other.invalid/" },
    { kind: "unsafe", message: "Unsafe", url: "/admin/notifications" },
  ] }) });
  await state.intervals[0].callback();
  assert.equal(state.banners.children.length, 0);
});

test("identical alerts do not replace focused DOM nodes every minute", async () => {
  const state = setup({ fetcher: async () => response({ unread_count: 1, alerts: [
    { kind: "warning", message: "Deadline", url: "/admin/notifications" },
  ] }) });
  await state.intervals[0].callback();
  const first = state.banners.children[0];
  await state.intervals[0].callback();
  assert.equal(state.banners.children[0], first);
});

for (const value of [-1, "3", 1.5, null]) {
  test(`invalid unread count ${JSON.stringify(value)} does not overwrite the current UI`, async () => {
    const state = setup({ fetcher: async () => response({ unread_count: value, alerts: [] }) });
    state.count.textContent = "Existing";
    await state.intervals[0].callback();
    assert.equal(state.count.textContent, "Existing");
  });
}

test("network errors and expired login HTML do not clear the existing indicator", async () => {
  for (const fetcher of [async () => { throw new Error("offline"); }, async () => response({}, { type: "text/html" })]) {
    const state = setup({ fetcher });
    state.count.textContent = "Existing";
    await state.intervals[0].callback();
    assert.equal(state.count.textContent, "Existing");
  }
});

test("non-dashboard management pages still update the bell", async () => {
  const state = setup({ dashboard: false });
  await state.intervals[0].callback();
  assert.equal(state.count.textContent, "3");
});
