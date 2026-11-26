// Bilder-Pool: Upload mit Fortschritt, Mehrfachauswahl, Großansicht.
(function () {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const fmtSize = (b) => (b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

  // --- Upload mit Fortschritt -------------------------------------------
  const uploadForm = $("#upload-form");
  if (uploadForm) {
    const input = $("#upload-input");
    const drop = $(".drop", uploadForm);
    const box = $("#upload-list");
    const items = $("#upload-items");
    const summary = $("#upload-summary");
    const done = $("#upload-done");
    let busy = false;

    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove("over")));
    drop.addEventListener("drop", (e) => { e.preventDefault(); start(Array.from(e.dataTransfer.files)); });
    input.addEventListener("change", () => { start(Array.from(input.files)); input.value = ""; });
    uploadForm.addEventListener("submit", (e) => { e.preventDefault(); start(Array.from(input.files)); });
    $("#upload-reload").addEventListener("click", () => location.reload());

    function row(file) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.className = "fname";
      name.textContent = `${file.name} (${fmtSize(file.size)})`;
      const bar = document.createElement("progress");
      bar.max = 100;
      bar.value = 0;
      const msg = document.createElement("span");
      msg.className = "msg muted";
      msg.textContent = "wartet …";
      li.append(name, bar, msg);
      items.appendChild(li);
      return li;
    }

    // Lädt eine Datei hoch. Ergebnis: "ok" | "dup" | "error"
    function send(file, li, allowDuplicate) {
      return new Promise((resolve) => {
        const bar = $("progress", li);
        const msg = $(".msg", li);
        li.className = "";
        bar.value = 0;
        msg.textContent = "wird hochgeladen …";

        const data = new FormData();
        data.append("files", file, file.name);
        if (allowDuplicate) data.append("allow_duplicate", "1");

        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/admin/pool/upload");
        xhr.setRequestHeader("Accept", "application/json");
        xhr.upload.onprogress = (e) => { if (e.lengthComputable) bar.value = (e.loaded / e.total) * 100; };
        xhr.upload.onload = () => { bar.removeAttribute("value"); msg.textContent = "wird verarbeitet …"; };

        const fail = (text) => { bar.value = 0; li.className = "error"; msg.textContent = text; resolve("error"); };
        xhr.onerror = () => fail("Verbindung fehlgeschlagen");
        xhr.onload = () => {
          if (xhr.responseURL.includes("/login")) return fail("Sitzung abgelaufen, bitte neu anmelden");
          let r;
          try { r = JSON.parse(xhr.responseText); } catch (_) { return fail(`Serverfehler (${xhr.status})`); }
          bar.value = 100;
          if (r.saved.length) {
            li.className = "ok";
            msg.textContent = r.saved[0] === file.name ? "hochgeladen" : `hochgeladen als „${r.saved[0]}“`;
            return resolve("ok");
          }
          if (r.duplicates.length) {
            li.className = "dup";
            msg.textContent = `Bereits im Pool als „${r.duplicates[0].existing}“, übersprungen.`;
            const again = document.createElement("button");
            again.type = "button";
            again.className = "dup-again";
            again.textContent = "Trotzdem hochladen";
            again.addEventListener("click", async () => {
              again.hidden = true;
              await send(file, li, true);
              done.hidden = false;
            });
            msg.appendChild(again);
            return resolve("dup");
          }
          fail(r.errors[0] || "Fehler beim Hochladen");
        };
        xhr.send(data);
      });
    }

    async function start(files) {
      files = files.filter((f) => f.size > 0);
      if (!files.length || busy) return;
      busy = true;
      box.hidden = false;
      done.hidden = true;
      items.textContent = "";
      const rows = files.map((f) => [f, row(f)]);
      const counts = { ok: 0, dup: 0, error: 0 };
      for (const [i, [file, li]] of rows.entries()) {
        summary.textContent = `${i + 1} von ${files.length}`;
        counts[await send(file, li, false)]++;
      }
      busy = false;
      const parts = [`${counts.ok} hochgeladen`];
      if (counts.dup) parts.push(`${counts.dup} doppelt`);
      if (counts.error) parts.push(`${counts.error} fehlgeschlagen`);
      summary.textContent = parts.join(" · ");
      // Alles glatt -> Seite neu laden; sonst Ergebnis stehen lassen
      if (!counts.dup && !counts.error) location.reload();
      else done.hidden = false;
    }
  }

  // --- Mehrfachauswahl & Löschen ----------------------------------------
  const bulk = $("#bulk-form");
  if (bulk) {
    const btn = $("#bulk-delete");
    const checked = () => $$("input[name=name]:checked", bulk);
    const update = () => {
      $(".sel-count", bulk).textContent = checked().length;
      btn.disabled = !checked().length;
    };
    bulk.addEventListener("change", update);
    $$("button[data-select]", bulk).forEach((b) => b.addEventListener("click", () => {
      $$(".pool-item:not(.hidden) input[name=name]", bulk).forEach((cb) => { cb.checked = b.dataset.select === "all"; });
      update();
    }));
    bulk.addEventListener("submit", (e) => {
      const cards = checked().map((cb) => cb.closest(".pool-item"));
      let text = cards.length === 1
        ? `„${cards[0].dataset.file}“ endgültig löschen?`
        : `${cards.length} Bilder endgültig löschen?`;
      const used = cards.filter((c) => c.dataset.users);
      if (used.length) {
        text += "\n\nDiese Bilder werden auch aus Zeiträumen entfernt:\n" +
          used.map((c) => `• ${c.dataset.file}: ${c.dataset.users.split("\n").join(", ")}`).join("\n");
      }
      if (!confirm(text)) e.preventDefault();
    });
  }

  // --- Großansicht ------------------------------------------------------
  const viewer = $("#viewer");
  if (viewer) {
    const cards = () => $$(".pool-item[data-file]:not(.hidden)");
    let index = 0;
    let info = null;

    async function open(i) {
      const list = cards();
      if (!list.length) return;
      index = (i + list.length) % list.length;
      const res = await fetch(`/admin/pool/info/${encodeURIComponent(list[index].dataset.file)}`);
      if (!res.ok) return;
      info = await res.json();

      $(".viewer-image img", viewer).src = info.display_url;
      $(".v-name", viewer).textContent = info.name;
      $(".v-res", viewer).textContent = info.width ? `${info.width} × ${info.height} px (${info.format})` : "unbekannt";
      $(".v-size", viewer).textContent = fmtSize(info.size);
      $(".v-date", viewer).textContent = info.modified;

      const users = $(".v-users", viewer);
      users.textContent = info.users.length ? "" : "nicht verwendet";
      if (info.users.length) {
        const ul = document.createElement("ul");
        info.users.forEach((u) => {
          const a = document.createElement("a");
          a.href = `/admin/schedules/${u.id}`;
          a.textContent = u.name;
          const li = document.createElement("li");
          li.appendChild(a);
          ul.appendChild(li);
        });
        users.appendChild(ul);
      }

      $$("input[name=name]", viewer).forEach((el) => { el.value = info.name; });
      $("input[name=new_name]", viewer).value = info.name;
      const defBtn = $(".v-default button", viewer);
      defBtn.disabled = info.is_default;
      defBtn.textContent = info.is_default ? "Ist Standardbild" : "Als Standardbild";
      $$(".nav", viewer).forEach((b) => { b.hidden = list.length < 2; });
      if (!viewer.open) viewer.showModal();
    }

    document.addEventListener("click", (e) => {
      const btn = e.target.closest(".open-viewer");
      if (btn) open(cards().indexOf(btn.closest(".pool-item")));
    });
    $$(".nav", viewer).forEach((b) => b.addEventListener("click", () => open(index + Number(b.dataset.step))));
    $(".close", viewer).addEventListener("click", () => viewer.close());
    viewer.addEventListener("click", (e) => { if (e.target === viewer) viewer.close(); });
    document.addEventListener("keydown", (e) => {
      if (!viewer.open || (e.target instanceof Element && e.target.matches("input, textarea"))) return;
      if (e.key === "ArrowLeft") open(index - 1);
      if (e.key === "ArrowRight") open(index + 1);
    });

    $(".v-delete", viewer).addEventListener("submit", (e) => {
      let text = `„${info.name}“ endgültig löschen?`;
      if (info.users.length) text += `\n\nEs wird auch aus diesen Zeiträumen entfernt:\n• ${info.users.map((u) => u.name).join("\n• ")}`;
      if (!confirm(text)) e.preventDefault();
    });

    // Beim Umbenennen nur den Namen ohne Endung markieren
    const renameInput = $("input[name=new_name]", viewer);
    renameInput.addEventListener("focus", () => {
      const dot = renameInput.value.lastIndexOf(".");
      renameInput.setSelectionRange(0, dot > 0 ? dot : renameInput.value.length);
    });
  }
})();
