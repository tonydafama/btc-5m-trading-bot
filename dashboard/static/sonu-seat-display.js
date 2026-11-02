/* Display-only Son U seat labels.
   Paper stays paper whether or not the ledger has fills.
   Does not call live-control POST or change trading mode. */
(function () {
  function rowIsLive(row) {
    if (!row || typeof row !== "object") return false;
    if (row.dry_run === true) return false;
    if (row.live_enabled === true) return true;
    var mode = String(row.mode || row.ledger || "").toLowerCase();
    if (mode === "live") return true;
    if (mode === "paper" || row.live_enabled === false) return false;
    return false;
  }

  function patchBoard(board) {
    var hl = board && board.high_live_board;
    if (!hl || !Array.isArray(hl.rows)) return board;
    var any = false;
    hl.rows.forEach(function (row) {
      if (!row) return;
      var live = rowIsLive(row);
      any = any || live;
      if (live) {
        if (row.change && /切換\s*→\s*Live|開真倉/.test(row.change) && !/暫停真倉/.test(row.change)) {
          row.change = "真倉 ON · 可切去 Paper（暫停真倉）";
        }
        return;
      }
      if (row.status_label && /真倉/.test(row.status_label) && !/紙上|未開|OFF/.test(row.status_label)) {
        row.status_label = "跑緊 · 紙上預備";
      }
      if (row.change && /切換\s*→\s*Paper|暫停真倉/.test(row.change)) {
        row.change = "紙上預備 · 真倉未開";
      }
      if (row.note && /切換\s*→\s*Paper|暫停真倉/.test(row.note)) {
        row.note = "紙上預備 · 真倉未開";
      }
    });
    hl.any_live = any;
    return board;
  }

  function decorateFill(row, raw) {
    if (!row) return row;
    var res = String(row.trade_result || "").toUpperCase();
    if (res === "WIN" || res === "LOSS" || res === "FAILED" || res === "FAIL" || res === "FILLED") {
      row.entered = true;
      return row;
    }
    if (res === "SKIPPED" || res === "SKIP") return row;
    var line = typeof raw === "string" ? raw : String(row.raw || row.reason || "");
    if (/\bSKIP\b/i.test(line)) return row;
    var pnl = line.match(/pnl=\s*([+-]?\d+(?:\.\d+)?)/i);
    if (!pnl) return row;
    var n = Number(pnl[1]);
    if (Number.isNaN(n)) return row;
    row.trade_result = n < 0 ? "LOSS" : "WIN";
    row.entered = true;
    row.pnl = n;
    return row;
  }

  var fixingDesk = false;
  function setText(el, value) {
    if (el && el.textContent !== value) el.textContent = value;
  }
  function fixDeskDom() {
    if (fixingDesk) return;
    var badge = document.getElementById("hl-badge");
    var body = document.getElementById("hl-body");
    if (!badge || !body) return;
    var html = body.innerHTML || "";
    var paperPrep = /PAPER PREP/.test(html);
    var livePill = /class="pill live"/.test(html);
    if (!paperPrep || livePill) return;
    fixingDesk = true;
    try {
      setText(badge, "PREP · PAPER");
      if (badge.className !== "hl-badge is-off") badge.className = "hl-badge is-off";
      badge.setAttribute("aria-label", "PREP PAPER — off air");
      var runEl = document.getElementById("hl-run");
      if (runEl && runEl.className !== "chip") runEl.className = "chip";
      setText(runEl, "紙上預備 RUNNING");
      var modeEl = document.getElementById("hl-mode");
      if (modeEl && modeEl.dataset.mode !== "paper") {
        modeEl.dataset.mode = "paper";
        setText(modeEl, "模式：Paper（紙上）");
        modeEl.title = "而家係 Paper · 撳掣可切去 Live（開真倉；要銀包≥$100）";
      }
      var btn = document.getElementById("btn-hl-toggle");
      if (btn && btn.dataset.mode !== "paper") {
        btn.dataset.mode = "paper";
        btn.dataset.target = "live";
        setText(btn, "切換 → Live（開真倉）");
        btn.title = "雙向切換 · 而家 Paper → 切去 Live（要銀包≥$100；唔夠會拒絕）";
      }
      var note = document.getElementById("hl-note");
      if (note && /真倉已開/.test(note.textContent || "")) {
        setText(note, note.textContent.replace("真倉已開", "真倉未開"));
      }
      body.querySelectorAll(".meta").forEach(function (el) {
        var t = el.textContent || "";
        if (/跑緊\s*·\s*真倉/.test(t)) setText(el, t.replace(/跑緊\s*·\s*真倉/, "跑緊 · 紙上預備"));
        else if (/切換\s*→\s*Paper|暫停真倉/.test(t)) setText(el, "紙上預備 · 真倉未開");
      });
    } finally {
      fixingDesk = false;
    }
  }

  function watchDesk() {
    if (!document.getElementById("hl-badge")) return;
    fixDeskDom();
    var root = document.getElementById("hl-wrap") || document.body;
    if (window.MutationObserver && !window.__sonuDeskObserver) {
      window.__sonuDeskObserver = new MutationObserver(function () { fixDeskDom(); });
      window.__sonuDeskObserver.observe(root, { childList: true, subtree: true, characterData: true });
    }
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", watchDesk);
    } else {
      watchDesk();
    }
  }

  if (window.__sonuSeatBuiltin) {
    window.sonuSeatDisplay = { rowIsLive: rowIsLive, patchBoard: patchBoard, decorateFill: decorateFill };
    return;
  }

  if (typeof window.parseLine === "function" && !window.parseLine._seatPatched) {
    var origParse = window.parseLine;
    window.parseLine = function (raw) {
      return decorateFill(origParse(raw), raw);
    };
    window.parseLine._seatPatched = true;
  }

  if (typeof window.paintLiveStatus === "function" && !window.paintLiveStatus._seatPatched) {
    var origPaint = window.paintLiveStatus;
    window.paintLiveStatus = function () {
      var ctrl = window.__sonuControl;
      var lineEl = document.getElementById("live-status-line");
      if (ctrl && lineEl && (ctrl.mode || ctrl.live_active != null)) {
        var live = ctrl.live_active === true || String(ctrl.mode || "").toLowerCase() === "live";
        var running = live ? ctrl.live_active !== false : ctrl.paper_active !== false;
        lineEl.textContent = (live ? "真倉 ON" : "紙上預備") + " · " + (running ? "跑緊" : "暫停");
        lineEl.classList.toggle("paused", !running);
        return;
      }
      return origPaint();
    };
    window.paintLiveStatus._seatPatched = true;
  }

  if (typeof window.refreshLiveUnit === "function" && !window.refreshLiveUnit._seatPatched) {
    var origRefresh = window.refreshLiveUnit;
    window.refreshLiveUnit = async function () {
      try {
        var res = await fetch("/api/son-u/live-control", { cache: "no-store" });
        if (res.ok) window.__sonuControl = await res.json();
      } catch (e) { /* status falls back to family-bots */ }
      return origRefresh();
    };
    window.refreshLiveUnit._seatPatched = true;
    if (typeof window.refreshAll === "function") window.refreshAll();
  }

  window.sonuSeatDisplay = { rowIsLive: rowIsLive, patchBoard: patchBoard, decorateFill: decorateFill };
})();
