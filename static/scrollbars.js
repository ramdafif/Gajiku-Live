(() => {
  "use strict";

  if (!("ResizeObserver" in window) || !("PointerEvent" in window)) return;

  const page = document.documentElement;
  const scroller = document.scrollingElement;
  if (!scroller || document.querySelector(".page-scrollbars")) return;

  if (!scroller.id) {
    let suffix = 0;
    let identifier = "page-scroll-region";
    while (document.getElementById(identifier)) {
      identifier = `page-scroll-region-${++suffix}`;
    }
    scroller.id = identifier;
  }

  const layer = document.createElement("div");
  layer.className = "page-scrollbars";
  const contrastQuery = window.matchMedia("(forced-colors: active)");
  const axes = [
    {
      name: "y",
      dimension: "height",
      clientSize: "clientHeight",
      viewportSize: "innerHeight",
      scrollSize: "scrollHeight",
      offset: "scrollTop",
      coordinate: "clientY",
      backward: "ArrowUp",
      forward: "ArrowDown",
      orientation: "vertical",
    },
    {
      name: "x",
      dimension: "width",
      clientSize: "clientWidth",
      viewportSize: "innerWidth",
      scrollSize: "scrollWidth",
      offset: "scrollLeft",
      coordinate: "clientX",
      backward: "ArrowLeft",
      forward: "ArrowRight",
      orientation: "horizontal",
    },
  ];
  let frame = 0;

  const update = () => {
    page.classList.toggle("has-floating-scrollbar", !contrastQuery.matches);
    const viewport = window.visualViewport;
    const bounds = {
      width: viewport?.width ?? page.clientWidth,
      height: viewport?.height ?? page.clientHeight,
      left: viewport?.offsetLeft ?? 0,
      top: viewport?.offsetTop ?? 0,
    };
    const pageStyle = getComputedStyle(page);
    const bodyStyle = getComputedStyle(document.body);

    axes.forEach((axis) => {
      const viewportSize = Math.max(page[axis.clientSize], window[axis.viewportSize]);
      axis.maximum = Math.max(0, scroller[axis.scrollSize] - viewportSize);
      const locked = [pageStyle, bodyStyle].some((style) =>
        ["hidden", "clip"].includes(style.getPropertyValue(`overflow-${axis.name}`)),
      );
      axis.visible = axis.maximum > 1 && !locked && !contrastQuery.matches;
    });

    axes.forEach((axis) => {
      axis.track.hidden = !axis.visible;
      if (!axis.visible) return;

      const other = axes.find((candidate) => candidate !== axis);
      const length = Math.max(0, bounds[axis.dimension] - 4 - (other.visible ? 12 : 0));
      const size = Math.min(
        length,
        Math.max(28, (bounds[axis.dimension] / scroller[axis.scrollSize]) * length),
      );
      axis.travel = length - size;
      axis.pageSize = bounds[axis.dimension];
      const progress = Math.max(0, Math.min(1, scroller[axis.offset] / axis.maximum));

      axis.track.style.left = `${bounds.left + (axis.name === "y" ? bounds.width - 12 : 2)}px`;
      axis.track.style.top = `${bounds.top + (axis.name === "x" ? bounds.height - 12 : 2)}px`;
      axis.track.style.width = `${axis.name === "x" ? length : 12}px`;
      axis.track.style.height = `${axis.name === "y" ? length : 12}px`;
      axis.thumb.style[axis.dimension] = `${size}px`;
      axis.thumb.style.transform = `translate${axis.name.toUpperCase()}(${progress * axis.travel}px)`;
      axis.thumb.setAttribute("aria-valuenow", String(Math.round(progress * 100)));
    });
  };

  const schedule = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      update();
    });
  };

  const scrollTo = (axis, offset) => {
    scroller.scrollTo({
      [axis.name === "y" ? "top" : "left"]: Math.max(0, Math.min(axis.maximum, offset)),
      behavior: "instant",
    });
    schedule();
  };

  axes.forEach((axis) => {
    const track = document.createElement("div");
    track.className = `page-scrollbar page-scrollbar-${axis.name}`;
    track.hidden = true;
    const thumb = document.createElement("div");
    thumb.className = "page-scrollbar-thumb";
    thumb.tabIndex = 0;
    thumb.setAttribute("role", "scrollbar");
    thumb.setAttribute("aria-label", `Gulir halaman ${axis.orientation === "vertical" ? "vertikal" : "horizontal"}`);
    thumb.setAttribute("aria-controls", scroller.id);
    thumb.setAttribute("aria-orientation", axis.orientation);
    thumb.setAttribute("aria-valuemin", "0");
    thumb.setAttribute("aria-valuemax", "100");
    thumb.setAttribute("aria-valuenow", "0");
    track.append(thumb);
    layer.append(track);
    Object.assign(axis, { track, thumb });

    thumb.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !axis.travel) return;
      event.preventDefault();
      axis.drag = {
        pointer: event[axis.coordinate],
        offset: scroller[axis.offset],
        identifier: event.pointerId,
      };
      page.classList.add("is-scrollbar-dragging");
      thumb.focus({ preventScroll: true });
      thumb.setPointerCapture(event.pointerId);
    });
    thumb.addEventListener("pointermove", (event) => {
      if (!axis.drag || axis.drag.identifier !== event.pointerId) return;
      const movement = event[axis.coordinate] - axis.drag.pointer;
      scrollTo(axis, axis.drag.offset + (movement * axis.maximum) / axis.travel);
    });
    const finishDrag = () => {
      axis.drag = null;
      page.classList.remove("is-scrollbar-dragging");
    };
    ["pointerup", "pointercancel", "lostpointercapture"].forEach((name) =>
      thumb.addEventListener(name, finishDrag),
    );
    thumb.addEventListener("keydown", (event) => {
      let offset = scroller[axis.offset];
      if (event.key === axis.backward) offset -= 40;
      else if (event.key === axis.forward) offset += 40;
      else if (event.key === "PageUp") offset -= axis.pageSize * 0.9;
      else if (event.key === "PageDown") offset += axis.pageSize * 0.9;
      else if (event.key === "Home") offset = 0;
      else if (event.key === "End") offset = axis.maximum;
      else if (event.key === " ") offset += axis.pageSize * (event.shiftKey ? -0.9 : 0.9);
      else return;
      event.preventDefault();
      scrollTo(axis, offset);
    });
  });

  document.body.append(layer);
  window.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("resize", schedule, { passive: true });
  window.addEventListener("pageshow", schedule);
  window.visualViewport?.addEventListener("resize", schedule, { passive: true });
  window.visualViewport?.addEventListener("scroll", schedule, { passive: true });
  contrastQuery.addEventListener("change", schedule);
  const resizeObserver = new ResizeObserver(schedule);
  resizeObserver.observe(page);
  resizeObserver.observe(document.body);
  const observer = new MutationObserver((mutations) => {
    if (mutations.some((mutation) => !layer.contains(mutation.target))) schedule();
  });
  observer.observe(document.body, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["class", "style", "open", "hidden"],
  });
  update();
})();
