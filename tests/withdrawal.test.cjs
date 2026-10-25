const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/withdrawal.js"), "utf8");

const element = (properties = {}) => ({
  value: "",
  textContent: "",
  listeners: new Map(),
  attributes: new Map(),
  addEventListener(name, callback) { this.listeners.set(name, callback); },
  setAttribute(name, value) { this.attributes.set(name, value); },
  removeAttribute(name) { this.attributes.delete(name); },
  getAttribute(name) { return this.attributes.get(name); },
  setCustomValidity(message) { this.validationMessage = message; },
  focus() {},
  ...properties,
});

const setup = ({ regMax, saldo = 2500000, product = "reg" }) => {
  const amount = element();
  const selectedProduct = element({ value: product });
  const account = element();
  const error = element();
  const label = element();
  const presets = ["100000", "250000", "500000", "max"].map((preset) =>
    element({ dataset: { preset } }),
  );
  const fields = { nominal: amount, produk: selectedProduct, rekening_tujuan_key: account };
  const form = element({
    elements: { namedItem: (name) => fields[name] },
    querySelectorAll: () => [selectedProduct],
  });
  const selectors = {
    "[data-withdrawal]": form,
    "[data-amount-error]": error,
    "[data-withdraw-confirm]": element(),
  };
  const config = element({ textContent: JSON.stringify({
    regMax, saldo, sisaPlafon: 3000000, plafon: 3000000,
    totalUsed: 0, dailyLimit: 100000, day: 25, fee: 15000, ppn: false,
  }) });
  const document = {
    querySelector: (selector) => selectors[selector],
    getElementById: (id) => id === "withdrawal-config" ? config : element(),
    querySelectorAll: (selector) => selector === "[data-preset]" ? presets
      : selector === "[data-limit-label]" ? [label] : [],
  };
  vm.runInNewContext(source, { document, Intl });
  return { amount, selectedProduct, error, label, presets };
};

for (const regMax of [500000, 1000000, 2000000]) {
  test(`maximum preset and validation use company limit ${regMax}`, () => {
    const state = setup({ regMax });
    state.presets.at(-1).listeners.get("click")();
    assert.equal(state.amount.value, new Intl.NumberFormat("id-ID").format(regMax));
    assert.equal(state.amount.validationMessage, "");
    state.amount.value = String(regMax + 1);
    state.amount.listeners.get("input")();
    assert.notEqual(state.amount.validationMessage, "");
    assert.equal(state.error.hidden, false);
  });
}

test("available balance caps maximum and disables larger presets", () => {
  const state = setup({ regMax: 2000000, saldo: 200000 });
  state.presets.at(-1).listeners.get("click")();
  assert.equal(state.amount.value, "200.000");
  assert.equal(state.presets[0].disabled, false);
  assert.equal(state.presets[1].disabled, true);
  assert.equal(state.presets[2].disabled, true);
});

test("urgent uses remaining plafond, then switching to regular restores company cap", () => {
  const state = setup({ regMax: 500000, product: "urg" });
  state.presets.at(-1).listeners.get("click")();
  assert.equal(state.amount.value, "3.000.000");
  assert.equal(state.amount.validationMessage, "");
  state.selectedProduct.value = "reg";
  state.selectedProduct.listeners.get("change")();
  assert.notEqual(state.amount.validationMessage, "");
  state.presets.at(-1).listeners.get("click")();
  assert.equal(state.amount.value, "500.000");
  assert.equal(state.amount.validationMessage, "");
});
