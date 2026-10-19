(() => {
  "use strict";

  const bell = document.querySelector("[data-notification-bell]");
  if (!bell) return;
  const count = bell.querySelector("[data-notification-count]");
  const banners = document.querySelector("[data-company-alerts]");
  let pending = false;
  let previousAlerts = null;

  const renderAlerts = (alerts) => {
    if (!banners || !Array.isArray(alerts)) return;
    const serialized = JSON.stringify(alerts);
    if (serialized === previousAlerts) return;
    const fragment = document.createDocumentFragment();
    for (const alert of alerts) {
      if (!alert || !["danger", "warning"].includes(alert.kind)) continue;
      const target = new URL(alert.url, window.location.origin);
      if (target.origin !== window.location.origin) continue;
      const link = document.createElement("a");
      link.className = `company-alert company-alert-${alert.kind}`;
      link.href = target.pathname + target.hash;
      const text = document.createElement("span");
      text.textContent = alert.message;
      link.append(text);
      fragment.append(link);
    }
    banners.replaceChildren(fragment);
    banners.hidden = !banners.childElementCount;
    previousAlerts = serialized;
  };

  const refresh = async () => {
    if (document.hidden || pending) return;
    pending = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(bell.dataset.summaryUrl, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!response.ok || !response.headers.get("content-type")?.includes("application/json")) return;
      const data = await response.json();
      if (!Number.isSafeInteger(data.unread_count) || data.unread_count < 0) return;
      count.textContent = data.unread_count > 99 ? "99+" : String(data.unread_count);
      count.hidden = data.unread_count === 0;
      bell.setAttribute("aria-label", `Notifikasi, ${data.unread_count} belum dibaca`);
      renderAlerts(data.alerts);
    } catch {
      return;
    } finally {
      clearTimeout(timeout);
      pending = false;
    }
  };

  setInterval(refresh, 60000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
})();
