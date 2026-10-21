const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../static/employees.js'), 'utf8');

function harness() {
  const listeners = {};
  const control = (name) => ({ checked: false, addEventListener: (event, callback) => { listeners[name] = callback; } });
  const reviewAll = control('reviewAll');
  const pageAll = control('pageAll');
  const decisions = { value: '' };
  const review = { addEventListener: (event, callback) => { listeners.review = callback; }, querySelector: () => decisions };
  const items = [{ checked: false }, { checked: false }];
  const employees = [{ checked: false }, { checked: false }];
  const allFiltered = { checked: true };
  const matches = {
    '[data-select-items]': [reviewAll], '[data-review-form]': [review], '[data-select-employees]': [pageAll],
    'input[name="item_ids"]': items, 'input[name="employee_ids"]': employees,
  };
  vm.runInNewContext(source, { document: {
    querySelectorAll: (selector) => matches[selector] || [], querySelector: () => allFiltered,
  } });
  return { listeners, reviewAll, pageAll, decisions, items, employees, allFiltered };
}

test('review all only selects pending review items', () => {
  const result = harness();
  result.reviewAll.checked = true;
  result.listeners.reviewAll();
  assert.equal(result.items.every((item) => item.checked), true);
  assert.equal(result.employees.some((item) => item.checked), false);
});

test('page selection disables cross-page select-all', () => {
  const result = harness();
  result.pageAll.checked = true;
  result.listeners.pageAll();
  assert.equal(result.employees.every((item) => item.checked), true);
  assert.equal(result.allFiltered.checked, false);
});

test('review decision survives submit button being disabled by the shared loading handler', () => {
  const result = harness();
  result.listeners.review({ submitter: { value: 'approve' } });
  assert.equal(result.decisions.value, 'approve');
  result.listeners.review({ submitter: { value: 'reject' } });
  assert.equal(result.decisions.value, 'reject');
  result.listeners.review({});
  assert.equal(result.decisions.value, '');
});
