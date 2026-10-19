(() => {
  "use strict";

  const select = (selector, scope = document) => scope.querySelector(selector);
  const selectAll = (selector, scope = document) =>
    Array.from(scope.querySelectorAll(selector));
  const rupiah = new Intl.NumberFormat("id-ID", {
    style: "currency",
    currency: "IDR",
    maximumFractionDigits: 0,
  });
  const parseData = (identifier) => {
    const element = document.getElementById(identifier);
    if (!element) return null;
    try {
      return JSON.parse(element.textContent);
    } catch {
      return null;
    }
  };

  const now = new Date();
  const dateFormatter = new Intl.DateTimeFormat("id-ID", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "Asia/Jakarta",
  });
  selectAll("[data-local-date]").forEach((element) => {
    element.textContent = dateFormatter.format(now);
  });
  selectAll("[data-current-year]").forEach((element) => {
    element.textContent = dateFormatter
      .formatToParts(now)
      .find((part) => part.type === "year").value;
  });

  const sidebar = select("#sidebar");
  const navToggle = select("[data-toggle-nav]");
  const navBackdrop = select(".sidebar-backdrop");
  const mobileQuery = window.matchMedia("(max-width: 900px)");
  const setNav = (open) => {
    if (!sidebar) return;
    const isOpen = open && mobileQuery.matches;
    document.body.classList.toggle("nav-open", isOpen);
    sidebar.inert = mobileQuery.matches && !isOpen;
    if (navBackdrop) navBackdrop.hidden = !isOpen;
    if (navToggle) navToggle.setAttribute("aria-expanded", String(isOpen));
    selectAll(".workspace, .bottom-nav").forEach((element) => {
      element.inert = isOpen;
    });
    if (isOpen) {
      sidebar.setAttribute("role", "dialog");
      sidebar.setAttribute("aria-modal", "true");
      select("a, button", sidebar)?.focus();
    } else {
      sidebar.removeAttribute("role");
      sidebar.removeAttribute("aria-modal");
    }
  };
  setNav(false);
  navToggle?.addEventListener("click", () =>
    setNav(!document.body.classList.contains("nav-open")),
  );
  selectAll("[data-close-nav]").forEach((button) =>
    button.addEventListener("click", () => {
      setNav(false);
      navToggle?.focus();
    }),
  );
  selectAll(".sidebar a").forEach((link) =>
    link.addEventListener("click", () => setNav(false)),
  );
  mobileQuery.addEventListener("change", () => setNav(false));
  document.addEventListener("keydown", (event) => {
    if (!document.body.classList.contains("nav-open")) return;
    if (event.key === "Escape") {
      setNav(false);
      navToggle?.focus();
    }
    if (event.key !== "Tab") return;
    const focusable = selectAll(
      'a[href], button:not([disabled]), [tabindex="0"]',
      sidebar,
    ).filter((element) => element.getClientRects().length);
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    }
    if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  });

  selectAll("[data-password]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = document.getElementById(button.dataset.password);
      if (!input) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.setAttribute("aria-pressed", String(show));
      const label =
        select(`label[for="${input.id}"]`)?.textContent || "password";
      button.setAttribute(
        "aria-label",
        `${show ? "Sembunyikan" : "Tampilkan"} ${label.toLowerCase()}`,
      );
    });
  });

  selectAll("[data-dismiss]").forEach((button) =>
    button.addEventListener("click", () => button.closest(".flash")?.remove()),
  );
  selectAll("[data-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = document.getElementById(button.dataset.dialog);
      if (!dialog || dialog.open) return;
      if (document.body.classList.contains("nav-open")) setNav(false);
      const form = select("form", dialog);
      if (form && button.dataset.formValues) {
        const values = JSON.parse(button.dataset.formValues);
        form.reset();
        selectAll("[data-temporary-option]", form).forEach((option) =>
          option.remove(),
        );
        if (button.dataset.formAction) {
          const action = new URL(
            button.dataset.formAction,
            window.location.origin,
          );
          if (action.origin !== window.location.origin) return;
          form.action = action.href;
        }
        const entries = Object.entries(values);
        const orderedEntries = [
          ...entries.filter(([name]) => name === "perusahaan_induk"),
          ...entries.filter(([name]) => name !== "perusahaan_induk"),
        ];
        orderedEntries.forEach(([name, value]) => {
          const field = form.elements.namedItem(name);
          if (!field || field instanceof RadioNodeList) return;
          if (
            field instanceof HTMLSelectElement &&
            value &&
            !Array.from(field.options).some(
              (option) => option.value === String(value),
            )
          ) {
            const option = new Option(`${value} (tersimpan)`, value);
            option.dataset.temporaryOption = "true";
            field.add(option);
          }
          if (field.type === "checkbox")
            field.checked = value === true || value === 1 || value === "1";
          else field.value = value ?? "";
          field.setCustomValidity("");
          if (name === "perusahaan_induk")
            field.dispatchEvent(new Event("change"));
        });
        const companyDisplay = select("[data-company-display]", form);
        if (companyDisplay)
          companyDisplay.value = values.perusahaan_induk || "";
      }
      dialog.showModal();
    });
  });
  selectAll("[data-close-dialog]").forEach((button) =>
    button.addEventListener("click", () => button.closest("dialog")?.close()),
  );

  selectAll("input[data-match]").forEach((input) => {
    const original = input.form?.elements.namedItem(input.dataset.match);
    const validate = () =>
      input.setCustomValidity(
        input.value && original && input.value !== original.value
          ? "Konfirmasi password belum cocok."
          : "",
      );
    input.addEventListener("input", validate);
    original?.addEventListener("input", validate);
  });
  selectAll('input[type="file"][data-max-bytes]').forEach((input) => {
    input.addEventListener("change", () => {
      input.setCustomValidity(
        input.files[0] && input.files[0].size > Number(input.dataset.maxBytes)
          ? "Ukuran gambar maksimal 2 MB."
          : "",
      );
      input.reportValidity();
    });
  });
  const validateRange = (form, startName, endName) => {
    const start = form.elements.namedItem(startName);
    const end = form.elements.namedItem(endName);
    if (!start || !end) return;
    const update = () => {
      end.min = start.value || "";
      end.setCustomValidity(
        start.value && end.value && end.value < start.value
          ? "Tanggal akhir tidak boleh sebelum tanggal mulai."
          : "",
      );
    };
    start.addEventListener("input", update);
    end.addEventListener("input", update);
    update();
  };
  selectAll("[data-date-range]").forEach((form) =>
    validateRange(form, "start", "end"),
  );
  selectAll("[data-mou-range]").forEach((form) =>
    validateRange(form, "mou_start", "mou_end"),
  );

  const catalog = parseData("client-catalog");
  if (Array.isArray(catalog)) {
    selectAll("[data-employee-form]").forEach((form) => {
      const company = form.elements.namedItem("perusahaan_induk");
      const project = select("[data-project-select]", form);
      if (!company || !project) return;
      company.addEventListener("change", () => {
        const client = catalog.find((item) => item.name === company.value);
        project.replaceChildren(
          new Option(
            client?.allow_projects
              ? "Pilih project resmi"
              : "Tidak menggunakan project",
            "",
          ),
        );
        if (client?.allow_projects)
          client.projects.forEach((name) =>
            project.add(new Option(name, name)),
          );
        project.required = Boolean(client?.allow_projects);
        select("[data-project-hint]", form).textContent =
          client?.allow_projects && !client.projects.length
            ? "Belum ada project. Tambahkan di Kelola project sebelum menyimpan pegawai."
            : "Project mengikuti perusahaan dan pengaturan MoU.";
      });
    });
  }

  let pendingForms = new WeakSet();
  const confirmedForms = new WeakSet();
  const originalButtons = new WeakMap();
  const confirmDialog = select("#confirm-dialog");
  let pendingConfirmation = null;
  const lockForm = (form) => {
    pendingForms.add(form);
    form.setAttribute("aria-busy", "true");
    selectAll('button[type="submit"], input[type="submit"]', form).forEach(
      (button) => {
        if (button.disabled) return;
        originalButtons.set(button, button.innerHTML);
        button.dataset.wasEnabled = "true";
        button.disabled = true;
        button.classList.add("is-loading");
        if (button.dataset.loadingText)
          button.textContent = button.dataset.loadingText;
      },
    );
  };
  document.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    const form = event.target;
    if (
      !(form instanceof HTMLFormElement) ||
      form.method.toLowerCase() === "get"
    )
      return;
    if (pendingForms.has(form)) {
      event.preventDefault();
      return;
    }
    if (form.dataset.confirm && !confirmedForms.has(form)) {
      event.preventDefault();
      pendingConfirmation = { form, submitter: event.submitter };
      select("#confirm-title").textContent =
        form.dataset.confirmTitle || "Lanjutkan tindakan?";
      select("[data-confirm-message]").textContent = form.dataset.confirm;
      confirmDialog.showModal();
      return;
    }
    confirmedForms.delete(form);
    lockForm(form);
  });
  select("[data-confirm-accept]")?.addEventListener("click", () => {
    const pending = pendingConfirmation;
    confirmDialog.close();
    pendingConfirmation = null;
    if (!pending || !pending.form.isConnected) return;
    confirmedForms.add(pending.form);
    pending.form.requestSubmit(pending.submitter || undefined);
  });
  confirmDialog?.addEventListener("close", () => {
    pendingConfirmation = null;
  });
  window.addEventListener("pageshow", () => {
    pendingForms = new WeakSet();
    selectAll('[aria-busy="true"]').forEach((form) =>
      form.removeAttribute("aria-busy"),
    );
    selectAll("[data-was-enabled]").forEach((button) => {
      button.disabled = false;
      button.classList.remove("is-loading");
      if (originalButtons.has(button))
        button.innerHTML = originalButtons.get(button);
      delete button.dataset.wasEnabled;
    });
  });

  selectAll("table[data-paginate]").forEach((table) => {
    const rows = selectAll("tbody > tr", table);
    const pageSize = Number(table.dataset.paginate) || 10;
    if (rows.length <= pageSize) return;
    let currentPage = 0;
    const pageCount = Math.ceil(rows.length / pageSize);
    const footer = document.createElement("div");
    footer.className = "table-footer";
    const count = document.createElement("span");
    count.setAttribute("aria-live", "polite");
    const controls = document.createElement("div");
    controls.className = "pagination";
    controls.setAttribute("aria-label", "Paginasi tabel");
    const previous = document.createElement("button");
    previous.type = "button";
    previous.className = "button button-secondary";
    previous.textContent = "Sebelumnya";
    const next = document.createElement("button");
    next.type = "button";
    next.className = "button button-secondary";
    next.textContent = "Berikutnya";
    const label = document.createElement("span");
    label.className = "pagination-label";
    controls.append(previous, label, next);
    footer.append(count, controls);
    table.closest(".table-scroll").after(footer);
    const renderPage = () => {
      rows.forEach((row, index) => {
        row.hidden =
          index < currentPage * pageSize ||
          index >= (currentPage + 1) * pageSize;
      });
      count.textContent = `${currentPage * pageSize + 1}-${Math.min((currentPage + 1) * pageSize, rows.length)} dari ${rows.length} data`;
      label.textContent = `${currentPage + 1} / ${pageCount}`;
      previous.disabled = currentPage === 0;
      next.disabled = currentPage === pageCount - 1;
    };
    previous.addEventListener("click", () => {
      currentPage -= 1;
      renderPage();
    });
    next.addEventListener("click", () => {
      currentPage += 1;
      renderPage();
    });
    renderPage();
  });

  const svgNamespace = "http://www.w3.org/2000/svg";
  const makeSvg = (name, attributes, text = "") => {
    const element = document.createElementNS(svgNamespace, name);
    Object.entries(attributes).forEach(([attribute, value]) =>
      element.setAttribute(attribute, String(value)),
    );
    if (text) element.textContent = text;
    return element;
  };
  const compact = new Intl.NumberFormat("id-ID", {
    notation: "compact",
    maximumFractionDigits: 1,
  });
  selectAll("[data-chart]").forEach((container) => {
    const data = parseData(container.dataset.chart);
    if (
      !Array.isArray(data?.values) ||
      !data.values.some((value) => Number(value) > 0)
    ) {
      container.className = "chart-empty";
      container.textContent = "Belum ada transaksi selesai pada periode ini.";
      return;
    }
    const values = data.values.map((value) => Math.max(Number(value) || 0, 0));
    const maximum = Math.max(...values);
    const chartWidth = 580;
    const chartHeight = 220;
    const paddingLeft = 52;
    const paddingBottom = 30;
    const paddingTop = 15;
    const plotWidth = chartWidth - paddingLeft - 10;
    const plotHeight = chartHeight - paddingBottom - paddingTop;
    const svg = makeSvg("svg", {
      viewBox: `0 0 ${chartWidth} ${chartHeight}`,
      "aria-hidden": "true",
    });
    [0, 0.5, 1].forEach((fraction) => {
      const vertical = paddingTop + plotHeight * (1 - fraction);
      svg.append(
        makeSvg("line", {
          x1: paddingLeft,
          y1: vertical,
          x2: chartWidth - 10,
          y2: vertical,
          stroke: "var(--chart-grid)",
          "stroke-dasharray": "3 5",
        }),
      );
      svg.append(
        makeSvg(
          "text",
          {
            x: paddingLeft - 10,
            y: vertical + 3,
            "text-anchor": "end",
            fill: "var(--chart-label)",
            "font-size": 9,
            "font-family": "inherit",
          },
          compact.format(maximum * fraction),
        ),
      );
    });
    const slotWidth = plotWidth / values.length;
    const labelStep = Math.max(1, Math.ceil(values.length / 9));
    values.forEach((value, index) => {
      const height = (value / maximum) * plotHeight;
      const horizontal = paddingLeft + index * slotWidth + slotWidth * 0.2;
      const width = Math.min(slotWidth * 0.6, 40);
      const bar = makeSvg("rect", {
        x: horizontal + (slotWidth * 0.6 - width) / 2,
        y: paddingTop + plotHeight - height,
        width,
        height,
        rx: 4,
        fill: index === values.length - 1 ? "var(--primary)" : "var(--chart-bar)",
      });
      bar.append(
        makeSvg("title", {}, `${data.labels[index]}: ${rupiah.format(value)}`),
      );
      svg.append(bar);
      if (index % labelStep === 0 || index === values.length - 1) {
        const rawLabel = String(data.labels[index] ?? "");
        const label = /^\d{4}-\d{2}$/.test(rawLabel)
          ? rawLabel.slice(5) + "/" + rawLabel.slice(2, 4)
          : rawLabel;
        svg.append(
          makeSvg(
            "text",
            {
              x: paddingLeft + index * slotWidth + slotWidth / 2,
              y: chartHeight - 8,
              "text-anchor": "middle",
              fill: "var(--chart-label)",
              "font-size": 9,
              "font-family": "inherit",
            },
            label,
          ),
        );
      }
    });
    container.replaceChildren(svg);
  });

  const pendingTransactions = selectAll(
    '[data-tx-id][data-tx-status="on-proses"]',
  );
  if (pendingTransactions.length) {
    const deadlines = new Map();
    selectAll("[data-countdown]").forEach((element) =>
      deadlines.set(
        element,
        performance.now() + Number(element.dataset.countdown) * 1000,
      ),
    );
    const updateCountdowns = () => {
      deadlines.forEach((deadline, element) => {
        const remaining = Math.max(
          0,
          Math.ceil((deadline - performance.now()) / 1000),
        );
        element.textContent = `${remaining}d`;
        if (!remaining) {
          const row = element.closest("[data-tx-id]");
          select("[data-cancel-form]", row).hidden = true;
          select("[data-processing-note]", row).hidden = false;
        }
      });
    };
    const statusLabels = {
      sukses: "Selesai",
      "on-proses": "Diproses",
      dibatalkan: "Dibatalkan",
      ditolak: "Ditolak",
      gagal: "Gagal",
    };
    let polling = false;
    const refreshStatuses = async () => {
      const activeRows = pendingTransactions.filter(
        (row) => row.dataset.txStatus === "on-proses",
      );
      if (document.hidden || polling || !activeRows.length) return;
      polling = true;
      try {
        const parameters = new URLSearchParams({
          ids: activeRows.map((row) => row.dataset.txId).join(","),
        });
        const response = await fetch(`/api/tx_status?${parameters}`, {
          credentials: "same-origin",
          cache: "no-store",
          headers: { Accept: "application/json" },
          signal: AbortSignal.timeout(8000),
        });
        if (!response.ok) return;
        const data = await response.json();
        if (!data.ok || !Array.isArray(data.items)) return;
        data.items.forEach((item) => {
          const row = pendingTransactions.find(
            (element) => element.dataset.txId === String(item.id),
          );
          if (!row || !Object.hasOwn(statusLabels, item.status)) return;
          row.dataset.txStatus = item.status;
          const badge = select("[data-status-badge]", row);
          const dot = document.createElement("span");
          dot.className = "status-dot";
          badge.replaceChildren(
            dot,
            document.createTextNode(statusLabels[item.status]),
          );
          badge.className = `badge ${item.status === "sukses" ? "badge-success" : item.status === "on-proses" ? "badge-warning" : item.status === "ditolak" || item.status === "gagal" ? "badge-danger" : "badge-neutral"}`;
          const form = select("[data-cancel-form]", row);
          const note = select("[data-processing-note]", row);
          if (form) form.hidden = !item.can_cancel;
          if (note) {
            note.hidden = Boolean(item.can_cancel);
            note.textContent =
              item.status === "on-proses" ? "Menunggu persetujuan" : "-";
          }
          const countdown = select("[data-countdown]", row);
          if (countdown)
            deadlines.set(
              countdown,
              performance.now() +
                Math.max(0, Number(item.remaining) || 0) * 1000,
            );
        });
        updateCountdowns();
      } catch {
        return;
      } finally {
        polling = false;
      }
    };
    updateCountdowns();
    setInterval(updateCountdowns, 1000);
    setInterval(refreshStatuses, 5000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) refreshStatuses();
    });
    refreshStatuses();
  }
})();
