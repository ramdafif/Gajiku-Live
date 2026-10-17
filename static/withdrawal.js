(() => {
  "use strict";

  const form = document.querySelector("[data-withdrawal]");
  const configElement = document.getElementById("withdrawal-config");
  if (!form || !configElement) return;
  const config = JSON.parse(configElement.textContent);
  const amountInput = form.elements.namedItem("nominal");
  const accountInput = form.elements.namedItem("rekening_tujuan_key");
  const error = document.querySelector("[data-amount-error]");
  const reviewDialog = document.getElementById("withdrawal-review");
  const confirmButton = document.querySelector("[data-withdraw-confirm]");
  const formatCurrency = new Intl.NumberFormat("id-ID", {
    style: "currency",
    currency: "IDR",
    maximumFractionDigits: 0,
  });
  const formatNumber = new Intl.NumberFormat("id-ID", {
    maximumFractionDigits: 0,
  });
  const amount = () => Number(amountInput.value.replace(/\D/g, "")) || 0;
  const product = () => form.elements.namedItem("produk").value;
  const limit = () =>
    product() === "reg"
      ? Math.min(config.saldo, config.regMax)
      : config.sisaPlafon;
  const fee = (nominal) => {
    if (!nominal) return 0;
    let baseFee = config.fee;
    if (product() === "urg") {
      const availableRegular = Math.max(
        config.dailyLimit * config.day - config.totalUsed,
        0,
      );
      const regularPortion = Math.min(nominal, availableRegular);
      const urgentPortion = Math.max(nominal - regularPortion, 0);
      baseFee =
        (regularPortion > 0 ? config.fee : 0) +
        (config.dailyLimit > 0
          ? Math.ceil(urgentPortion / config.dailyLimit) * config.fee
          : 0);
    }
    return config.ppn ? Math.floor((baseFee * 111 + 99) / 100) : baseFee;
  };
  const setText = (selector, value) =>
    document.querySelectorAll(selector).forEach((element) => {
      element.textContent = value;
    });
  const update = (showError = false) => {
    const nominal = amount();
    const message =
      nominal > limit()
        ? `Nominal melebihi batas ${formatCurrency.format(limit())}.`
        : nominal <= 0 && showError
          ? "Masukkan nominal pengajuan lebih dari Rp0."
          : "";
    amountInput.setCustomValidity(message);
    error.hidden = !message;
    error.textContent = message;
    if (message) amountInput.setAttribute("aria-invalid", "true");
    else amountInput.removeAttribute("aria-invalid");
    setText("[data-limit-label]", formatCurrency.format(limit()));
    setText(
      "[data-preview-amount], [data-review-amount]",
      formatCurrency.format(nominal),
    );
    setText(
      "[data-preview-fee], [data-review-fee]",
      formatCurrency.format(fee(nominal)),
    );
    setText(
      "[data-preview-total], [data-review-total]",
      formatCurrency.format(nominal + fee(nominal)),
    );
    document.querySelectorAll("[data-preset]").forEach((button) => {
      button.disabled =
        (button.dataset.preset !== "max" &&
          Number(button.dataset.preset) > limit()) ||
        limit() <= 0;
    });
    return !message && nominal > 0;
  };
  amountInput.addEventListener("input", () => {
    const digits = amountInput.value.replace(/\D/g, "").slice(0, 10);
    amountInput.value = digits ? formatNumber.format(Number(digits)) : "";
    update();
  });
  form
    .querySelectorAll('input[name="produk"]')
    .forEach((input) => input.addEventListener("change", () => update()));
  document.querySelectorAll("[data-preset]").forEach((button) => {
    button.addEventListener("click", () => {
      amountInput.value = formatNumber.format(
        button.dataset.preset === "max"
          ? limit()
          : Number(button.dataset.preset),
      );
      update();
      amountInput.focus();
    });
  });
  let reviewed = false;
  form.addEventListener("submit", (event) => {
    if (form.getAttribute("aria-busy") === "true") {
      event.preventDefault();
      return;
    }
    if (reviewed) {
      reviewed = false;
      return;
    }
    event.preventDefault();
    if (!update(true) || !form.reportValidity()) return;
    const selectedAccount = accountInput.options[accountInput.selectedIndex];
    setText("[data-review-account]", selectedAccount.textContent);
    setText(
      "[data-review-product]",
      product() === "reg" ? "Reguler (REG)" : "Urgent (URG)",
    );
    reviewDialog.showModal();
  });
  confirmButton.addEventListener("click", () => {
    if (
      !update(true) ||
      !form.reportValidity() ||
      form.getAttribute("aria-busy") === "true"
    )
      return;
    reviewed = true;
    reviewDialog.close();
    form.requestSubmit();
  });
  update();
})();
