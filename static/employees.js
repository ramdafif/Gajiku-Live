document.querySelectorAll('[data-select-items]').forEach((control) => {
  control.addEventListener('change', () => {
    document.querySelectorAll('input[name="item_ids"]').forEach((item) => { item.checked = control.checked; });
  });
});
document.querySelectorAll('[data-review-form]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    form.querySelector('input[name="decision"]').value = event.submitter?.value || '';
  });
});
document.querySelectorAll('[data-select-employees]').forEach((control) => {
  control.addEventListener('change', () => {
    document.querySelectorAll('input[name="employee_ids"]').forEach((item) => { item.checked = control.checked; });
    const allFiltered = document.querySelector('input[name="all_filtered"]');
    if (allFiltered) allFiltered.checked = false;
  });
});
