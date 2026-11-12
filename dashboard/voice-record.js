/* voice-record.js - Dedicated "Ghi am" (Record) page.

   Records speech via JavisDictate (polish: false) into raw text, lets the user
   review/append takes before normalizing, persists the raw transcript into the
   shared server SQLite audit database (POST /voice-records/normalize), writes
   VoiceRecord/YYYY.MM.DD-HH.MM.SS.md in the active brain, locks recording on
   that normalized item until "Tao ban ghi am moi" is clicked, and embeds the
   shared #noteEditor for editing the normalized Markdown file.
*/
(function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function tw(k, vars) {
    if (typeof window !== "undefined" && typeof window.t === "function") {
      return window.t(k, vars);
    }
    var s = String(k || "");
    if (vars && typeof vars === "object") {
      Object.keys(vars).forEach(function (key) {
        s = s.replace(new RegExp("\\{" + key + "\\}", "g"), String(vars[key]));
      });
    }
    return s;
  }

  function ico(name, opts) {
    if (typeof window !== "undefined" && typeof window.ic === "function") {
      return window.ic(name, opts);
    }
    return "";
  }

  function fmtTime(sec) {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    var s = sec % 60;
    return String(Math.floor(sec / 60)).padStart(2, "0") + ":" + (s < 10 ? "0" : "") + s;
  }

  function joinRawText(base, spoken) {
    base = String(base == null ? "" : base);
    spoken = String(spoken == null ? "" : spoken).trim();
    if (!spoken) return base;
    if (!base.trim()) return spoken;
    return base + (/\s$/.test(base) ? "" : " ") + spoken;
  }

  function isBusy(st) {
    var s = (st && st.state) || "idle";
    return s === "starting" || s === "finishing" || s === "normalizing";
  }

  function canRecord(st) {
    st = st || {};
    if (st.locked) return false;
    return st.state === "idle" || st.state === "recording";
  }

  function canCancelTake(st) {
    st = st || {};
    return !st.locked && st.state === "recording";
  }

  function canNormalize(st) {
    st = st || {};
    if (st.locked || st.state !== "idle") return false;
    return String(st.rawText || "").trim().length > 0;
  }

  function canNewRecord(st) {
    st = st || {};
    return st.state === "idle";
  }

  function levelOf(bytes) {
    if (typeof window !== "undefined" && window.JavisComposerDictate && window.JavisComposerDictate.levelOf) {
      return window.JavisComposerDictate.levelOf(bytes);
    }
    if (!bytes || !bytes.length) return 0;
    var sum = 0;
    for (var i = 0; i < bytes.length; i++) {
      var x = (bytes[i] - 128) / 128;
      sum += x * x;
    }
    var v = Math.sqrt(Math.sqrt(sum / bytes.length)) * 1.6 - 0.12;
    return v < 0 ? 0 : v > 1 ? 1 : v;
  }

  function basename(p) {
    var s = String(p || "").replace(/\\/g, "/");
    var idx = s.lastIndexOf("/");
    return idx >= 0 ? s.slice(idx + 1) : s;
  }

  function fmtDateLabel(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return String(iso);
      var loc = (typeof window !== "undefined" && window.JavisI18n && window.JavisI18n.locale)
        ? window.JavisI18n.locale() : "vi-VN";
      return d.toLocaleString(loc, {
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit"
      });
    } catch (e) {
      return String(iso);
    }
  }

  var pure = {
    fmtTime: fmtTime,
    joinRawText: joinRawText,
    isBusy: isBusy,
    canRecord: canRecord,
    canCancelTake: canCancelTake,
    canNormalize: canNormalize,
    canNewRecord: canNewRecord,
    levelOf: levelOf,
    basename: basename,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = pure;
  if (typeof document === "undefined") return;

  function activeBrain() {
    try {
      if (typeof window.currentBrainPath === "function") return window.currentBrainPath() || "brain";
      var sel = document.getElementById("graphSource");
      if (sel && sel.value) return sel.value;
    } catch (e) {}
    return "brain";
  }

  var S = {
    host: null,
    mounted: false,
    state: "idle", // idle | starting | recording | finishing | normalizing
    locked: false,
    rawText: "",
    baseBeforeTake: "",
    takeText: "",
    takeCommitted: false,
    takeCancelled: false,
    errorMsg: "",
    infoNote: "",
    activeRecord: null,
    editorClosed: false,
    history: [],
    historyLoading: false,
    dict: null,
    startedAt: 0,
    clock: null,
    ctx: null,
    src: null,
    analyser: null,
    buf: null,
    raf: 0,
    levels: [],
    lastPush: 0,
  };

  function $(id) {
    return S.host ? S.host.querySelector("#" + id) : null;
  }

  function stopClock() {
    if (S.clock) { clearInterval(S.clock); S.clock = null; }
  }

  function startClock() {
    stopClock();
    S.startedAt = Date.now();
    var el = $("vrTimer");
    if (el) el.textContent = "00:00";
    S.clock = setInterval(function () {
      var tEl = $("vrTimer");
      if (tEl) tEl.textContent = fmtTime((Date.now() - S.startedAt) / 1000);
    }, 250);
  }

  function drawMeter() {
    var cv = $("vrMeter");
    if (!cv) return;
    var g = cv.getContext("2d");
    if (!g) return;
    var w = cv.width, h = cv.height;
    g.clearRect(0, 0, w, h);
    var accent = "#f57c2d";
    try {
      var c = getComputedStyle(cv).color;
      if (c) accent = c;
    } catch (e) {}
    g.fillStyle = accent;
    var bars = 20, step = w / bars, bw = Math.max(2, Math.floor(step * 0.58));
    for (var i = 0; i < bars; i++) {
      var lv = S.levels[S.levels.length - bars + i] || 0;
      var bh = Math.max(3, Math.round(lv * (h - 4)));
      var x = Math.round(i * step + (step - bw) / 2);
      var y = Math.round((h - bh) / 2);
      g.fillRect(x, y, bw, bh);
    }
  }

  function stopMeter() {
    if (S.raf) { cancelAnimationFrame(S.raf); S.raf = 0; }
    try { if (S.src) S.src.disconnect(); } catch (e) {}
    S.src = null;
    S.analyser = null;
    S.buf = null;
    S.levels = [];
    drawMeter();
  }

  function onStream(stream) {
    if (!stream) { stopMeter(); return; }
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!S.ctx) S.ctx = new AC();
      if (S.ctx.state === "suspended" && S.ctx.resume) S.ctx.resume();
      S.src = S.ctx.createMediaStreamSource(stream);
      S.analyser = S.ctx.createAnalyser();
      S.analyser.fftSize = 256;
      S.buf = new Uint8Array(S.analyser.fftSize);
      S.src.connect(S.analyser);
      S.lastPush = 0;
      var tick = function (now) {
        if (!S.analyser || !S.mounted) return;
        if (now - S.lastPush > 55) {
          S.lastPush = now;
          S.analyser.getByteTimeDomainData(S.buf);
          S.levels.push(levelOf(S.buf));
          if (S.levels.length > 24) S.levels.shift();
          drawMeter();
        }
        S.raf = requestAnimationFrame(tick);
      };
      S.raf = requestAnimationFrame(tick);
    } catch (e) { stopMeter(); }
  }

  function statusLabel() {
    if (S.state === "starting") return tw("vr.status.starting");
    if (S.state === "recording") return tw("vr.status.recording");
    if (S.state === "finishing") return tw("vr.status.finishing");
    if (S.state === "normalizing") return tw("vr.status.normalizing");
    if (S.locked) return tw("vr.status.done");
    return tw("vr.status.idle");
  }

  function errorText(code) {
    var D = window.JavisDictate;
    var ios = D && D.isIOS && D.isIOS();
    var k = code === "denied" ? (ios ? "dict.denied_ios" : "dict.denied")
      : code === "service" ? (ios ? "dict.service_ios" : "dict.service")
      : code === "unsupported" ? "dict.unsupported"
      : code === "nothing" ? "dict.nothing"
      : "dict.failed";
    return tw(k);
  }

  function syncUi() {
    if (!S.host || !S.mounted) return;
    var dot = $("vrStatusDot");
    var stxt = $("vrStatusText");
    var timer = $("vrTimer");
    var meter = $("vrMeter");
    var recBtn = $("vrBtnRecord");
    var cancelBtn = $("vrBtnCancelTake");
    var normBtn = $("vrBtnNormalize");
    var newBtn = $("vrBtnNew");
    var rawEl = $("vrRawInput");
    var lockBadge = $("vrLockBadge");
    var liveHint = $("vrLiveHint");
    var lockNote = $("vrLockNote");
    var errEl = $("vrError");
    var edCard = $("vrEditorCard");
    var edFile = $("vrAuditFile");
    var edTime = $("vrAuditTime");
    var edNote = $("vrAuditFallback");
    var closedBar = $("vrClosedNotice");
    var vrEdit = $("vrEdit");

    if (dot) {
      dot.className = "vr-dot " + (S.state === "recording" ? "is-rec" : (isBusy(S) ? "is-busy" : (S.locked ? "is-done" : "is-idle")));
    }
    if (stxt) stxt.textContent = statusLabel();
    if (timer) timer.hidden = S.state !== "recording" && S.state !== "finishing";
    if (meter) meter.hidden = S.state !== "recording";

    if (recBtn) {
      var isRec = S.state === "recording";
      recBtn.classList.toggle("is-rec", isRec);
      recBtn.disabled = !canRecord(S) || isBusy(S);
      recBtn.innerHTML = (isRec ? ico("square") : ico("mic")) + " <span>" + esc(isRec ? tw("vr.btn.stop") : tw("vr.btn.record")) + "</span>";
      recBtn.title = S.locked ? tw("vr.raw.locked_note") : "";
    }
    if (cancelBtn) {
      cancelBtn.hidden = !canCancelTake(S);
      cancelBtn.disabled = !canCancelTake(S);
    }
    if (normBtn) {
      normBtn.disabled = !canNormalize(S);
      normBtn.innerHTML = ico("sparkles") + " <span>" + esc(S.state === "normalizing" ? tw("vr.btn.normalizing") : tw("vr.btn.normalize")) + "</span>";
    }
    if (newBtn) {
      newBtn.disabled = !canNewRecord(S);
    }
    if (rawEl) {
      if (rawEl.value !== S.rawText) rawEl.value = S.rawText;
      rawEl.readOnly = S.locked || S.state !== "idle";
      rawEl.classList.toggle("is-locked", S.locked);
    }
    if (lockBadge) lockBadge.hidden = !S.locked;
    if (liveHint) liveHint.hidden = S.state !== "recording";
    if (lockNote) lockNote.hidden = !S.locked;

    if (errEl) {
      errEl.hidden = !S.errorMsg;
      if (S.errorMsg) {
        errEl.innerHTML = ico("triangle-alert", { cls: "ic-warn" }) + " <span>" + esc(S.errorMsg) + "</span>";
      } else {
        errEl.innerHTML = "";
      }
    }

    if (edCard) {
      var hasRec = !!S.activeRecord;
      edCard.hidden = !hasRec;
      if (hasRec) {
        if (edFile) edFile.textContent = S.activeRecord.file_path || "";
        if (edTime) edTime.textContent = fmtDateLabel(S.activeRecord.created_iso);
        if (edNote) {
          edNote.hidden = !S.infoNote;
          edNote.textContent = S.infoNote || "";
        }
        if (closedBar) closedBar.hidden = !S.editorClosed;
        if (vrEdit) vrEdit.hidden = !!S.editorClosed;
      }
    }

    renderHistory();
  }

  function renderHistory() {
    var list = $("vrHistoryList");
    var cnt = $("vrHistoryCount");
    if (!list) return;
    if (cnt) cnt.textContent = tw("vr.history.count", { n: S.history.length });
    if (S.historyLoading && !S.history.length) {
      list.innerHTML = '<div class="vr-hist-empty">' + esc(tw("common.loading")) + "</div>";
      return;
    }
    if (!S.history.length) {
      list.innerHTML = '<div class="vr-hist-empty">' + esc(tw("vr.history.empty")) + "</div>";
      return;
    }
    var activeId = S.activeRecord ? S.activeRecord.id : "";
    list.innerHTML = S.history.map(function (r) {
      var act = activeId && r.id === activeId ? " active" : "";
      var snippet = String(r.raw_text || "").replace(/\s+/g, " ").trim();
      if (snippet.length > 120) snippet = snippet.slice(0, 120) + "...";
      return '<button type="button" class="vr-hist-item' + act + '" data-vrid="' + esc(r.id) + '">' +
        '<div class="vr-hist-top">' +
          '<span class="vr-hist-file">' + ico("file-text") + " <span>" + esc(basename(r.file_path) || r.file_path) + "</span></span>" +
          '<span class="vr-hist-time">' + esc(fmtDateLabel(r.created_iso)) + "</span>" +
        "</div>" +
        '<div class="vr-hist-raw">' + esc(snippet) + "</div>" +
      "</button>";
    }).join("");

    list.querySelectorAll(".vr-hist-item").forEach(function (btn) {
      btn.onclick = function () {
        if (isBusy(S) || S.state === "recording") return;
        var id = btn.getAttribute("data-vrid");
        var rec = S.history.find(function (x) { return x.id === id; });
        if (rec) openRecordInEditor(rec);
      };
    });
  }

  function openRecordInEditor(rec) {
    if (!rec) return;
    S.activeRecord = rec;
    S.locked = true;
    S.rawText = String(rec.raw_text || "");
    S.errorMsg = "";
    S.editorClosed = false;
    syncUi();

    var slot = $("vrEdit");
    var rel = rec.ceil_path || rec.file_path || "";
    var name = basename(rel) || "record.md";
    if (slot && rel && window.JavisNoteEditor && typeof window.JavisNoteEditor.openInSlot === "function") {
      window.JavisNoteEditor.openInSlot(
        slot,
        rel,
        { name: name, ext: ".md", type: "file" },
        {
          inPlace: true,
          reload: true,
          onClose: function () {
            if (!S.mounted) return;
            S.editorClosed = true;
            syncUi();
          },
        }
      );
    }
  }

  function startRecording() {
    if (!canRecord(S) || S.state !== "idle") return;
    var D = window.JavisDictate;
    if (!D || typeof D.create !== "function") {
      S.errorMsg = errorText("unsupported");
      syncUi();
      return;
    }

    S.errorMsg = "";
    S.infoNote = "";
    S.baseBeforeTake = S.rawText;
    S.takeText = "";
    S.takeCommitted = false;
    S.takeCancelled = false;

    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (AC && !S.ctx) S.ctx = new AC();
      if (S.ctx && S.ctx.state !== "running" && S.ctx.resume) S.ctx.resume();
    } catch (e) {}

    var live = !(D.isPhoneUA && D.isPhoneUA());
    S.state = "starting";
    syncUi();

    S.dict = D.create({
      polish: false,
      onText: function (text, isFinal) {
        if (!S.mounted || S.takeCancelled) return;
        S.takeText = String(text || "");
        if (isFinal) {
          S.takeCommitted = true;
          S.rawText = joinRawText(S.baseBeforeTake, S.takeText);
          syncUi();
        } else if (live && (S.state === "recording" || S.state === "starting")) {
          S.rawText = joinRawText(S.baseBeforeTake, S.takeText);
          var rawEl = $("vrRawInput");
          if (rawEl) {
            rawEl.value = S.rawText;
            rawEl.scrollTop = rawEl.scrollHeight;
          }
        }
      },
      onState: function (st) {
        if (!S.mounted) return;
        if (st === "listening") {
          S.state = "recording";
          startClock();
          syncUi();
        } else if (st === "transcribing") {
          stopClock();
          stopMeter();
          S.state = "finishing";
          syncUi();
        } else if (st === "idle") {
          stopClock();
          stopMeter();
          if (!S.takeCancelled && !S.takeCommitted && S.takeText) {
            S.rawText = joinRawText(S.baseBeforeTake, S.takeText);
          }
          if (S.takeCancelled) {
            S.rawText = S.baseBeforeTake;
          }
          S.state = "idle";
          S.dict = null;
          syncUi();
        }
      },
      onError: function (code) {
        if (!S.mounted) return;
        S.errorMsg = errorText(code);
        syncUi();
      },
      onStream: onStream,
    });

    S.dict.start();
  }

  function stopRecording() {
    if (S.dict && (S.state === "recording" || S.state === "starting")) {
      S.dict.stop();
    }
  }

  function cancelCurrentTake() {
    if (!S.dict || S.state !== "recording") return;
    S.takeCancelled = true;
    S.rawText = S.baseBeforeTake;
    stopClock();
    stopMeter();
    S.dict.cancel();
    S.state = "idle";
    S.dict = null;
    syncUi();
  }

  async function normalizeCurrent() {
    if (!canNormalize(S)) {
      if (!String(S.rawText || "").trim() && !S.locked) {
        S.errorMsg = tw("vr.err.empty");
        syncUi();
      }
      return;
    }

    S.state = "normalizing";
    S.errorMsg = "";
    S.infoNote = "";
    syncUi();

    var brain = activeBrain();
    var res = null;
    var okHttp = false;
    try {
      var r = await fetch("/voice-records/normalize", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ brain: brain, raw_text: S.rawText }),
      });
      res = await r.json().catch(function () { return {}; });
      okHttp = r.ok;
    } catch (e) {
      res = { ok: false, error: e.message || tw("vr.err.normalize") };
    }

    if (!S.mounted) return;
    S.state = "idle";

    if (!okHttp || !res || !res.ok || !res.record) {
      S.errorMsg = (res && res.error) || tw("vr.err.normalize");
      syncUi();
      return;
    }

    S.locked = true;
    if (!res.ai) {
      S.infoNote = tw("vr.audit.fallback_note");
    }
    var rec = res.record;
    S.history = [rec].concat(S.history.filter(function (x) { return x.id !== rec.id; }));
    openRecordInEditor(rec);
    refreshHistory();
  }

  function resetNewRecording() {
    if (!canNewRecord(S)) return;
    try {
      if (window.JavisNoteEditor && typeof window.JavisNoteEditor.closeInSlot === "function") {
        window.JavisNoteEditor.closeInSlot();
      }
    } catch (e) {}
    S.locked = false;
    S.rawText = "";
    S.baseBeforeTake = "";
    S.takeText = "";
    S.errorMsg = "";
    S.infoNote = "";
    S.activeRecord = null;
    S.editorClosed = false;
    syncUi();
    var rawEl = $("vrRawInput");
    if (rawEl) try { rawEl.focus(); } catch (e) {}
  }

  async function refreshHistory() {
    if (!S.mounted) return;
    S.historyLoading = true;
    renderHistory();
    try {
      var r = await fetch("/voice-records?brain=" + encodeURIComponent(activeBrain()) + "&limit=30", {
        cache: "no-store",
        credentials: "same-origin",
      });
      var d = await r.json().catch(function () { return {}; });
      if (!S.mounted) return;
      if (r.ok && d && Array.isArray(d.records)) {
        S.history = d.records;
      }
    } catch (e) {
      // Keep existing history if offline
    } finally {
      S.historyLoading = false;
      if (S.mounted) renderHistory();
    }
  }

  function mount(el) {
    unmount();
    S.host = el;
    S.mounted = true;
    S.state = "idle";
    S.locked = false;
    S.rawText = "";
    S.baseBeforeTake = "";
    S.takeText = "";
    S.errorMsg = "";
    S.infoNote = "";
    S.activeRecord = null;
    S.editorClosed = false;
    S.history = [];

    el.innerHTML =
      '<div class="vr-page">' +
        '<div class="vr-grid">' +
          '<div class="vr-main">' +
            '<div class="gcard vr-controls-card">' +
              '<div class="vr-bar-top">' +
                '<div class="vr-status">' +
                  '<span class="vr-dot is-idle" id="vrStatusDot" aria-hidden="true"></span>' +
                  '<span class="vr-status-text" id="vrStatusText"></span>' +
                  '<span class="vr-timer" id="vrTimer" hidden>00:00</span>' +
                  '<canvas class="vr-meter" id="vrMeter" width="120" height="26" hidden></canvas>' +
                "</div>" +
                '<div class="vr-ctas">' +
                  '<button type="button" class="vr-btn vr-btn-rec" id="vrBtnRecord">' +
                    ico("mic") + " <span>" + esc(tw("vr.btn.record")) + "</span>" +
                  "</button>" +
                  '<button type="button" class="gcard-btn ghost" id="vrBtnCancelTake" hidden>' +
                    ico("x") + " <span>" + esc(tw("vr.btn.cancel_take")) + "</span>" +
                  "</button>" +
                  '<button type="button" class="vr-btn vr-btn-norm" id="vrBtnNormalize" disabled>' +
                    ico("sparkles") + " <span>" + esc(tw("vr.btn.normalize")) + "</span>" +
                  "</button>" +
                  '<button type="button" class="gcard-btn" id="vrBtnNew">' +
                    ico("plus") + " <span>" + esc(tw("vr.btn.new")) + "</span>" +
                  "</button>" +
                "</div>" +
              "</div>" +
              '<div class="vr-error" id="vrError" hidden></div>' +
              '<div class="vr-lock-note" id="vrLockNote" hidden>' +
                ico("lock") + " <span>" + esc(tw("vr.raw.locked_note")) + "</span>" +
              "</div>" +
            "</div>" +
            '<div class="gcard vr-raw-card">' +
              '<div class="vr-card-head">' +
                '<div class="vr-card-title">' + esc(tw("vr.raw.title")) + "</div>" +
                '<div class="vr-card-meta">' +
                  '<span class="vr-live-hint" id="vrLiveHint" hidden>' + esc(tw("vr.raw.live")) + "</span>" +
                  '<span class="vr-badge-lock" id="vrLockBadge" hidden>' + ico("lock") + " " + esc(tw("vr.raw.locked_badge")) + "</span>" +
                "</div>" +
              "</div>" +
              '<textarea id="vrRawInput" class="vr-raw-input" rows="7" data-esc placeholder="' + esc(tw("vr.raw.placeholder")) + '"></textarea>' +
            "</div>" +
            '<div class="gcard vr-editor-card" id="vrEditorCard" hidden>' +
              '<div class="vr-card-head vr-ed-head">' +
                '<div class="vr-card-title">' + ico("circle-check", { cls: "ic-ok" }) + " <span>" + esc(tw("vr.audit.title")) + "</span></div>" +
                '<div class="vr-audit-meta">' +
                  '<span class="vr-audit-file" id="vrAuditFile"></span>' +
                  '<span class="vr-audit-time" id="vrAuditTime"></span>' +
                "</div>" +
              "</div>" +
              '<div class="vr-audit-sub">' + esc(tw("vr.audit.saved")) + "</div>" +
              '<div class="vr-audit-fallback" id="vrAuditFallback" hidden></div>' +
              '<div class="vr-closed-notice" id="vrClosedNotice" hidden>' +
                "<span>" + esc(tw("vr.editor.closed")) + "</span>" +
                '<button type="button" class="gcard-btn" id="vrBtnReopen">' + ico("file-text") + " " + esc(tw("vr.btn.open_editor")) + "</button>" +
              "</div>" +
              '<div class="vr-edit" id="vrEdit" data-ne-host="1"></div>' +
            "</div>" +
          "</div>" +
          '<aside class="gcard vr-history-card">' +
            '<div class="vr-card-head">' +
              '<div class="vr-card-title">' + ico("clock") + " <span>" + esc(tw("vr.history.title")) + "</span></div>" +
              '<span class="vr-hist-count" id="vrHistoryCount"></span>' +
            "</div>" +
            '<div class="vr-hist-list" id="vrHistoryList"></div>' +
          "</aside>" +
        "</div>" +
      "</div>";

    var recBtn = $("vrBtnRecord");
    var cancelBtn = $("vrBtnCancelTake");
    var normBtn = $("vrBtnNormalize");
    var newBtn = $("vrBtnNew");
    var rawEl = $("vrRawInput");
    var reopenBtn = $("vrBtnReopen");

    if (recBtn) {
      recBtn.onclick = function () {
        if (S.state === "idle" && !S.locked) startRecording();
        else if (S.state === "recording") stopRecording();
      };
    }
    if (cancelBtn) cancelBtn.onclick = cancelCurrentTake;
    if (normBtn) normBtn.onclick = normalizeCurrent;
    if (newBtn) newBtn.onclick = resetNewRecording;
    if (reopenBtn) reopenBtn.onclick = function () { if (S.activeRecord) openRecordInEditor(S.activeRecord); };
    if (rawEl) {
      rawEl.addEventListener("input", function () {
        if (S.locked || S.state !== "idle") return;
        S.rawText = rawEl.value;
        var nb = $("vrBtnNormalize");
        if (nb) nb.disabled = !canNormalize(S);
      });
    }

    syncUi();
    refreshHistory();
  }

  function unmount() {
    S.mounted = false;
    stopClock();
    stopMeter();
    if (S.dict) {
      try { S.dict.cancel(); } catch (e) {}
      S.dict = null;
    }
    try {
      if (window.JavisNoteEditor && typeof window.JavisNoteEditor.closeInSlot === "function") {
        window.JavisNoteEditor.closeInSlot();
      }
    } catch (e) {}
    S.state = "idle";
    S.host = null;
  }

  window.JavisVoiceRecord = Object.assign({ mount: mount, unmount: unmount }, pure);
})();

