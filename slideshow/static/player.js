// Player: holt regelmäßig die aktuelle Playlist und zeigt sie in Schleife.
// Ändert sich die Playlist (neuer Zeitraum beginnt/endet, Bearbeitung im Admin),
// wird beim nächsten Bildwechsel umgeschaltet und von vorn begonnen.
(function () {
  "use strict";
  const POLL_MS = 15000;
  const RELOAD_AFTER_MS = 24 * 3600 * 1000; // täglich neu laden gegen Speicherlecks

  const layers = [document.getElementById("layer-a"), document.getElementById("layer-b")];
  const status = document.getElementById("status");
  const setup = document.getElementById("setup");
  // Läuft der Player lokal auf dem Pi, den Netzwerknamen statt "localhost" anzeigen
  const adminUrl = document.getElementById("admin-url");
  const local = ["localhost", "127.0.0.1", "::1"].includes(location.hostname);
  const host = local ? `${adminUrl.dataset.hostname}.local` : location.hostname;
  adminUrl.textContent = `http://${host}${location.port ? ":" + location.port : ""}/admin`;
  const startedAt = Date.now();

  let current = null;   // aktive Playlist
  let pending = null;   // neue Playlist, wird beim nächsten Wechsel übernommen
  let index = -1;
  let front = 0;
  let timer = null;

  function applyStyle(p) {
    layers.forEach((l) => {
      l.style.objectFit = p.fit === "cover" ? "cover" : "contain";
      l.style.transitionDuration = `${p.transition_ms}ms`;
    });
  }

  function show(src) {
    const cur = layers[front];
    if (cur.classList.contains("visible") && cur.getAttribute("src") === src) return;
    const back = layers[1 - front];
    back.src = src;
    back.classList.add("visible");
    cur.classList.remove("visible");
    front = 1 - front;
  }

  function blank() {
    layers.forEach((l) => l.classList.remove("visible"));
    setup.style.display = current && current.setup_needed ? "grid" : "none";
  }

  function next() {
    clearTimeout(timer);
    if (Date.now() - startedAt > RELOAD_AFTER_MS) { location.reload(); return; }
    if (pending) { current = pending; pending = null; index = -1; applyStyle(current); }
    if (!current || current.slides.length === 0) { blank(); return; }

    index = (index + 1) % current.slides.length;
    setup.style.display = "none";
    const slide = current.slides[index];
    const img = new Image();
    img.onload = () => {
      show(slide.url);
      timer = setTimeout(next, slide.duration * 1000);
    };
    img.onerror = () => { timer = setTimeout(next, 2000); };
    img.src = slide.url;
  }

  function onPlaylist(p) {
    if (current && p.version === current.version) { pending = null; return; }
    if (pending && p.version === pending.version) return;
    if (!current || current.slides.length === 0) {
      current = p; pending = null; index = -1; applyStyle(p); next();
    } else {
      pending = p;
    }
  }

  async function poll() {
    try {
      const res = await fetch("/api/playlist", { cache: "no-store" });
      if (!res.ok) throw new Error(res.status);
      onPlaylist(await res.json());
      status.style.display = "none";
    } catch (e) {
      status.style.display = "block";
    }
  }

  // Vollbild: Browser erlauben das nur nach einer Benutzeraktion.
  // Auf dem Pi startet Chromium bereits im Kiosk-Modus (deploy/kiosk.sh).
  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen().catch(() => {});
  }
  document.addEventListener("dblclick", toggleFullscreen);
  document.addEventListener("keydown", (e) => { if (e.key === "f" || e.key === "F") toggleFullscreen(); });

  poll();
  setInterval(poll, POLL_MS);
})();
