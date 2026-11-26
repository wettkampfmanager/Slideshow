// Admin-Oberfläche: kleine Helfer ohne externe Bibliotheken.
(function () {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // Rückfrage vor destruktiven Aktionen
  $$("form[data-confirm]").forEach((f) =>
    f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); })
  );

  // --- Zeitraum-Formular: nur relevante Felder zeigen -------------------
  const kind = $("#kind");
  if (kind) {
    // Nicht passende Felder ausblenden und deaktivieren (damit "required" dort nicht greift)
    const update = () => $$("[data-kinds]").forEach((el) => {
      const off = !el.dataset.kinds.split(" ").includes(kind.value);
      el.hidden = off;
      if (el.tagName === "FIELDSET") el.disabled = off;
    });
    kind.addEventListener("change", update);
    update();
    $("#allday")?.addEventListener("click", () => { $("#start_time").value = "00:00"; $("#end_time").value = "23:59"; });
    $$("button[data-days]").forEach((b) => b.addEventListener("click", () => {
      const days = b.dataset.days.split(",");
      $$("input[name=weekdays]").forEach((cb) => { cb.checked = days.includes(cb.value); });
    }));
  }

  // --- Slides: Reihenfolge & Dauer --------------------------------------
  const list = $("#slides");
  if (list) {
    const scheduleId = list.dataset.schedule;
    const total = $("#total");

    const post = (url, data) => fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
    });
    const saveOrder = () => post(`/admin/schedules/${scheduleId}/slides/order`, {
      order: $$("li", list).map((li) => li.dataset.id),
    });
    const updateTotal = () => {
      const def = parseInt(total.dataset.default, 10);
      const sec = $$("li:not(.missing) input[data-slide]", list).reduce((s, i) => s + (parseInt(i.value, 10) || def), 0);
      total.textContent = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")} min`;
    };

    let dragged = null;
    list.addEventListener("dragstart", (e) => {
      dragged = e.target.closest("li");
      dragged.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
    });
    list.addEventListener("dragend", () => {
      dragged?.classList.remove("dragging");
      $$("li.over", list).forEach((li) => li.classList.remove("over"));
      dragged = null;
    });
    list.addEventListener("dragover", (e) => {
      const li = e.target.closest("li");
      if (!dragged || !li || li === dragged) return;
      e.preventDefault();
      const rect = li.getBoundingClientRect();
      const after = e.clientY > rect.top + rect.height / 2;
      li.parentNode.insertBefore(dragged, after ? li.nextSibling : li);
    });
    list.addEventListener("drop", (e) => { e.preventDefault(); saveOrder(); });

    list.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-move]");
      if (!btn) return;
      const li = btn.closest("li");
      if (btn.dataset.move === "-1" && li.previousElementSibling) list.insertBefore(li, li.previousElementSibling);
      else if (btn.dataset.move === "1" && li.nextElementSibling) list.insertBefore(li.nextElementSibling, li);
      else return;
      saveOrder();
      btn.focus();
    });

    list.addEventListener("change", async (e) => {
      const input = e.target.closest("input[data-slide]");
      if (!input) return;
      const label = input.closest(".dur");
      label.classList.remove("saved", "error");
      const res = await post(`/admin/slides/${input.dataset.slide}/duration`, { duration: input.value || null });
      label.classList.add(res.ok ? "saved" : "error");
      updateTotal();
    });
  }

  // --- Pool: Filter & Auswahl -------------------------------------------
  $$(".pool-filter").forEach((input) => input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    $$(".pool-item[data-name]").forEach((it) => it.classList.toggle("hidden", q && !it.dataset.name.includes(q)));
  }));

  const picker = $(".picker");
  if (picker) {
    const count = () => { $(".sel-count", picker).textContent = $$("input[name=filenames]:checked", picker).length; };
    picker.addEventListener("change", count);
    $$("button[data-select]", picker).forEach((b) => b.addEventListener("click", () => {
      $$(".pool-item:not(.hidden) input[name=filenames]", picker).forEach((cb) => { cb.checked = b.dataset.select === "all"; });
      count();
    }));
  }
})();
