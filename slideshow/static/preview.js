// Vorschau: Zeitleiste zum Verschieben über den Tag.
(function () {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const tl = $("#timeline");
  if (!tl) return;
  const date = tl.dataset.date;
  const scrub = $("#tl-scrub");
  const clock = $("#tl-time");
  const result = $("#preview-result");
  const pad = (n) => String(n).padStart(2, "0");
  const fmt = (m) => `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;

  // Datum wechseln -> Seite mit gleicher Uhrzeit neu laden
  $("#tl-date").addEventListener("change", (e) => {
    if (e.target.value) location.href = `/admin/preview?at=${e.target.value}T${fmt(minute)}`;
  });
  let minute = Number(tl.dataset.minute);
  if (!scrub) return;

  const cursor = $(".tl-cursor", scrub);
  const labels = $$(".tl-label", tl);
  const rows = $$(".tl-track", scrub).map((el) => ({
    el, segs: JSON.parse(el.dataset.segments), closure: el.classList.contains("closure"),
  }));
  const isOn = (segs, m) => segs.some(([a, b]) => m >= a && m <= b);
  const activeKey = (m) => rows.map((r) => (isOn(r.segs, m) ? "1" : "0")).join("");

  // Zeitpunkte, an denen sich die Playlist ändert
  const changes = [...new Set(rows.flatMap((r) => r.segs.flatMap(([a, b]) => [a, b + 1])))]
    .filter((m) => m > 0 && m < 1440)
    .sort((x, y) => x - y);
  const prevBtn = $("#tl-prev-change");
  const nextBtn = $("#tl-next-change");

  let lastKey = activeKey(minute);
  let timer = null;
  let controller = null;

  function load() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      controller?.abort();
      controller = new AbortController();
      try {
        const res = await fetch(`/admin/preview/result?at=${date}T${fmt(minute)}`, { signal: controller.signal });
        if (res.ok) result.innerHTML = await res.text();
      } catch (_) { /* abgebrochen oder offline */ }
    }, 120);
  }

  function setMinute(m) {
    minute = Math.max(0, Math.min(1439, Math.round(m)));
    const t = fmt(minute);
    cursor.style.left = `${((minute + 0.5) / 1440) * 100}%`;
    clock.textContent = t;
    scrub.setAttribute("aria-valuenow", minute);
    scrub.setAttribute("aria-valuetext", t);
    // Während einer Schließzeit sind alle anderen aktiven Zeiträume unterdrückt
    const closed = rows.some((r) => r.closure && isOn(r.segs, minute));
    rows.forEach((r, i) => {
      const on = isOn(r.segs, minute);
      const suppressed = on && closed && !r.closure;
      r.el.classList.toggle("on", on && !suppressed);
      r.el.classList.toggle("suppressed", suppressed);
      labels[i].classList.toggle("on", on && !suppressed);
      labels[i].classList.toggle("suppressed", suppressed);
    });
    $$(".day-link").forEach((a) => { a.href = `/admin/preview?at=${a.dataset.day}T${t}`; });
    history.replaceState(null, "", `/admin/preview?at=${date}T${t}`);

    // Uhrzeit in der Überschrift sofort, Inhalt nur bei geänderter Playlist nachladen
    const h2 = $("h2", result);
    if (h2) h2.textContent = h2.textContent.replace(/\d{2}:\d{2}$/, t);
    const key = activeKey(minute);
    if (key !== lastKey) { lastKey = key; load(); }
  }

  // Maus / Finger
  const fromEvent = (e) => {
    const rect = scrub.getBoundingClientRect();
    return ((e.clientX - rect.left) / rect.width) * 1440;
  };
  let dragging = false;
  scrub.addEventListener("pointerdown", (e) => {
    dragging = true;
    scrub.setPointerCapture(e.pointerId);
    setMinute(fromEvent(e));
  });
  scrub.addEventListener("pointermove", (e) => { if (dragging) setMinute(fromEvent(e)); });
  ["pointerup", "pointercancel"].forEach((ev) => scrub.addEventListener(ev, () => { dragging = false; }));

  // Tastatur
  scrub.addEventListener("keydown", (e) => {
    const steps = { ArrowLeft: -1, ArrowRight: 1, ArrowDown: -1, ArrowUp: 1, PageDown: -60, PageUp: 60 };
    if (e.key in steps) setMinute(minute + steps[e.key] * (e.shiftKey ? 15 : 1));
    else if (e.key === "Home") setMinute(0);
    else if (e.key === "End") setMinute(1439);
    else return;
    e.preventDefault();
  });

  // Wechsel springen: innerhalb des Tages sofort, sonst den Server über Tagesgrenzen suchen lassen
  async function jump(direction, btn) {
    const local = direction > 0 ? changes.find((x) => x > minute) : changes.filter((x) => x < minute).pop();
    if (local !== undefined) { setMinute(local); return; }
    btn.disabled = true;
    try {
      const res = await fetch(`/admin/preview/change?at=${date}T${fmt(minute)}&direction=${direction}`);
      const { at } = await res.json();
      if (!at) {
        btn.title = direction > 0 ? "Kein weiterer Wechsel in den nächsten 400 Tagen" : "Kein früherer Wechsel in den letzten 400 Tagen";
        return;
      }
      const [d, hm] = at.split("T");
      if (d === date) { setMinute(Number(hm.slice(0, 2)) * 60 + Number(hm.slice(3, 5))); btn.disabled = false; }
      else location.href = `/admin/preview?at=${at}`;
    } catch (_) {
      btn.disabled = false;
    }
  }
  prevBtn.addEventListener("click", () => jump(-1, prevBtn));
  nextBtn.addEventListener("click", () => jump(1, nextBtn));
  prevBtn.title = "Zum vorherigen Wechsel springen (auch an früheren Tagen)";
  nextBtn.title = "Zum nächsten Wechsel springen (auch an späteren Tagen)";

  setMinute(minute);
})();
