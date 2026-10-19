(() => {
  "use strict";

  const storageKey = "gajiku-color-scheme";
  const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
  const root = document.documentElement;
  const validTheme = (value) => value === "light" || value === "dark";
  let preference = null;

  try {
    const stored = window.localStorage.getItem(storageKey);
    if (validTheme(stored)) preference = stored;
  } catch {}

  const currentTheme = () =>
    preference || (systemTheme.matches ? "dark" : "light");

  const applyTheme = () => {
    const theme = currentTheme();
    const dark = theme === "dark";
    root.dataset.theme = theme;
    document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => {
      meta.setAttribute("content", dark ? "#17212b" : "#f6f8fb");
      meta.removeAttribute("media");
    });
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      const label = dark ? "Aktifkan mode terang" : "Aktifkan mode gelap";
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
      button.setAttribute("aria-pressed", String(dark));
      button.hidden = false;
    });
  };

  applyTheme();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", applyTheme, { once: true });
  }

  document.addEventListener("click", (event) => {
    if (!event.target.closest?.("[data-theme-toggle]")) return;
    preference = currentTheme() === "dark" ? "light" : "dark";
    applyTheme();
    try {
      window.localStorage.setItem(storageKey, preference);
    } catch {}
  });

  systemTheme.addEventListener("change", () => {
    if (preference === null) applyTheme();
  });

  window.addEventListener("storage", (event) => {
    if (event.key !== storageKey && event.key !== null) return;
    if (event.storageArea) {
      try {
        if (event.storageArea !== window.localStorage) return;
      } catch {
        return;
      }
    }
    preference = validTheme(event.newValue) ? event.newValue : null;
    applyTheme();
  });
})();
