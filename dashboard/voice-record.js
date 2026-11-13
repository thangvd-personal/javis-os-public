/* voice-record.js - Dedicated "Ghi am" (Record) page.

   Supports two modes on one page:
     1) Quick Note ("quick"): low-latency dictation via JavisDictate -> SQLite audit
        -> AI grammar normalization -> VoiceRecord/YYYY.MM.DD-HH.MM.SS.md + #noteEditor.
     2) Long Meeting ("meeting"): MediaRecorder (Pause/Resume) or audio file upload
        in 8 MB Blob slices -> chunked server upload -> GPU/CPU faster-whisper
        (large-v3-turbo / medium cached in local-ear) + pyannote diarization ->
        audio playback, 30-day pin toggle, speaker mapping, AI normalize keeping
        [MM:SS - Speaker] prefixes, and SRT/VTT/TXT/JSON export.

   Note: Do NOT use the em dash character (U+2014) anywhere in this file.
*/
(function () {
  "use strict";

  var CHUNK_SIZE_BYTES = 8 * 1024 * 1024; // 8 MB per upload slice

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
    var h = Math.floor(sec / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = sec % 60;
    if (h > 0) {
      return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    }
    return String(m).padStart(2, "0") + ":" + (s < 10 ? "0" : "") + s;
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

  function canMeetingStart(mt) {
    mt = mt || {};
    return mt.recState === "idle" && !mt.uploading && !mt.normalizing;
  }

  function canMeetingPause(mt) {
    mt = mt || {};
    return mt.recState === "recording";
  }

  function canMeetingResume(mt) {
    mt = mt || {};
    return mt.recState === "paused";
  }

  function canMeetingStop(mt) {
    mt = mt || {};
    return mt.recState === "recording" || mt.recState === "paused";
  }

  function getInstallCtaState(mt, st) {
    mt = mt || {};
    st = st || {};
    var model = mt.model || "large-v3-turbo";
    var statusLoaded = Boolean(st.ok);
    var activeAction = mt.installPending || (st.installing ? (st.install_mode || "all") : "");
    var busy = Boolean(mt.installPending || st.installing || mt.deletingModel);
    var pyannoteReady = Boolean(st.venv_diarize_ready);
    var cachedMap = st.cached_whisper_models || {};
    var modelCached = Boolean(cachedMap[model]);
    var libReady = Boolean(st.lib_ready);

    return {
      busy: busy,
      pyannoteReady: pyannoteReady,
      modelCached: modelCached,
      libReady: libReady,
      installAll: {
        visible: !pyannoteReady || (busy && activeAction === "all"),
        disabled: !statusLoaded || busy || pyannoteReady,
        lockedDone: !busy && pyannoteReady,
        labelKey: (busy && activeAction === "all")
          ? "vr.meeting.btn_installing"
          : (pyannoteReady ? "vr.meeting.btn_pyannote_installed" : "vr.meeting.btn_install_all"),
      },
      installModel: {
        visible: true,
        disabled: !statusLoaded || busy || modelCached || !libReady,
        lockedDone: !busy && modelCached,
        labelKey: (busy && (activeAction === "model" || activeAction === "whisper"))
          ? "vr.meeting.btn_downloading_model"
          : (modelCached ? "vr.meeting.btn_model_installed" : "vr.meeting.btn_install_model"),
      },
      redownloadModel: {
        visible: modelCached || (busy && activeAction === "redownload"),
        disabled: !statusLoaded || busy || !libReady,
        labelKey: (busy && activeAction === "redownload")
          ? "vr.meeting.btn_redownloading"
          : "vr.meeting.btn_redownload_model",
      },
      deleteModel: {
        visible: modelCached,
        disabled: !statusLoaded || busy,
        labelKey: mt.deletingModel
          ? "vr.meeting.btn_deleting_model"
          : "vr.meeting.btn_delete_model",
      },
    };
  }

  function sliceBlobChunks(blobOrFile, chunkSize) {
    var sz = Math.max(1024, Number(chunkSize || CHUNK_SIZE_BYTES));
    var total = Number((blobOrFile && blobOrFile.size) || 0);
    if (!total) return [];
    var slices = [];
    for (var off = 0; off < total; off += sz) {
      var end = Math.min(total, off + sz);
      slices.push(typeof blobOrFile.slice === "function" ? blobOrFile.slice(off, end) : blobOrFile);
    }
    return slices;
  }

  function buildSpeakerMapPayload(speakers, inputsById) {
    var out = {};
    (speakers || []).forEach(function (sp) {
      var sid = String((sp && sp.id) || "").trim();
      if (!sid) return;
      var val = inputsById && inputsById[sid] != null ? String(inputsById[sid]).trim() : "";
      if (val && val !== sid) out[sid] = val;
    });
    return out;
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

  function renderSetupHintHtml() {
    var dUrl = "https://huggingface.co/pyannote/speaker-diarization-3.1";
    var sUrl = "https://huggingface.co/pyannote/segmentation-3.0";
    var dTitle = tw("vr.meeting.hint_diarize_title");
    var sTitle = tw("vr.meeting.hint_seg_title");
    var dLink = '<a href="' + dUrl + '" target="_blank" rel="noopener noreferrer" class="vr-link" title="' + esc(dTitle) + '">pyannote/speaker-diarization-3.1 ↗</a>';
    var sLink = '<a href="' + sUrl + '" target="_blank" rel="noopener noreferrer" class="vr-link" title="' + esc(sTitle) + '">pyannote/segmentation-3.0 ↗</a>';
    return esc(tw("vr.meeting.setup_hint_prefix")) + " " + dLink + " " + esc(tw("vr.meeting.setup_hint_and")) + " " + sLink + " " + esc(tw("vr.meeting.setup_hint_suffix"));
  }

  var pure = {
    CHUNK_SIZE_BYTES: CHUNK_SIZE_BYTES,
    fmtTime: fmtTime,
    joinRawText: joinRawText,
    isBusy: isBusy,
    canRecord: canRecord,
    canCancelTake: canCancelTake,
    canNormalize: canNormalize,
    canNewRecord: canNewRecord,
    canMeetingStart: canMeetingStart,
    canMeetingPause: canMeetingPause,
    canMeetingResume: canMeetingResume,
    canMeetingStop: canMeetingStop,
    getInstallCtaState: getInstallCtaState,
    sliceBlobChunks: sliceBlobChunks,
    buildSpeakerMapPayload: buildSpeakerMapPayload,
    levelOf: levelOf,
    basename: basename,
    renderSetupHintHtml: renderSetupHintHtml,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = pure;
  if (typeof document === "undefined") return;

  function activeBrain() {
    try {
      if (typeof window.currentBrainPath === "function") return window.currentBrainPath() || "brain";
      var sel = document.getElementById("graphSource");
      if (sel && sel.value) return sel.value;
    } catch (e) { }
    return "brain";
  }

  var S = {
    host: null,
    mounted: false,
    tab: "quick", // quick | meeting
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
    mt: {
      recState: "idle", // idle | recording | paused
      uploading: false,
      normalizing: false,
      rediarizing: false,
      uploadDone: 0,
      uploadTotal: 0,
      title: "",
      model: "large-v3-turbo",
      language: "vi",
      numSpeakers: 0,
      diarize: true,
      diarizeTouched: false,
      drawerOpen: false,
      statusData: null,
      activeJob: null,
      jobPollTimer: null,
      installPollTimer: null,
      installPending: "",
      installPendingModel: "",
      installNotice: null,
      pollErrCount: 0,
      errorMsg: "",
      warnMsg: "",
      elapsedMs: 0,
      resumeAt: 0,
      mtClock: null,
      stream: null,
      mr: null,
      chunks: [],
      segments: [],
      mimeType: "audio/webm",
      cancelled: false,
      speakerDraft: {},
    },
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

  function stopMtClock() {
    if (S.mt.mtClock) { clearInterval(S.mt.mtClock); S.mt.mtClock = null; }
  }

  function mtElapsedSec() {
    var ms = S.mt.elapsedMs;
    if (S.mt.recState === "recording" && S.mt.resumeAt) {
      ms += (Date.now() - S.mt.resumeAt);
    }
    return Math.floor(ms / 1000);
  }

  function startMtClock() {
    stopMtClock();
    S.mt.mtClock = setInterval(function () {
      var el = $("vrMtTimer");
      if (el) el.textContent = fmtTime(mtElapsedSec());
    }, 250);
  }

  function drawMeter(canvasId) {
    var cv = $(canvasId || "vrMeter");
    if (!cv) return;
    var g = cv.getContext("2d");
    if (!g) return;
    var w = cv.width, h = cv.height;
    g.clearRect(0, 0, w, h);
    var accent = "#f57c2d";
    try {
      var c = getComputedStyle(cv).color;
      if (c) accent = c;
    } catch (e) { }
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
    try { if (S.src) S.src.disconnect(); } catch (e) { }
    S.src = null;
    S.analyser = null;
    S.buf = null;
    S.levels = [];
    drawMeter("vrMeter");
    drawMeter("vrMtMeter");
  }

  function startMeter(stream, canvasId) {
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
          drawMeter(canvasId);
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

  function jobStatusLabel(job) {
    if (!job) return tw("vr.status.idle");
    var st = job.status;
    if (st === "uploading") return tw("vr.meeting.status_uploading", { done: S.mt.uploadDone || job.received_chunks || 0, total: S.mt.uploadTotal || job.total_chunks || 1 });
    if (st === "queued") return tw("vr.meeting.status_queued");
    if (st === "converting") return tw("vr.meeting.status_converting");
    if (st === "transcribing") return tw("vr.meeting.status_transcribing", { model: job.model || S.mt.model });
    if (st === "diarizing") return tw("vr.meeting.status_diarizing");
    if (st === "merging") return tw("vr.meeting.status_merging");
    if (st === "failed") return tw("vr.meeting.status_failed");
    if (st === "done") return tw("vr.status.done");
    return st || "";
  }

  function warningTextForCodes(codes) {
    if (!Array.isArray(codes) || !codes.length) return "";
    var msgs = [];
    codes.forEach(function (c) {
      var s = String(c || "");
      if (s === "missing_hf_token") msgs.push(tw("vr.meeting.token_missing_warn"));
      else if (s.indexOf("pyannote_not_installed") === 0) msgs.push(tw("vr.meeting.diarize_not_installed_warn"));
      else if (s.indexOf("pyannote_auth_error") === 0) msgs.push(tw("vr.meeting.pyannote_auth_warn"));
      else if (s) msgs.push(s);
    });
    return msgs.join(" ");
  }

  function syncTabs() {
    if (!S.host || !S.mounted) return;
    var tq = $("vrTabQuick");
    var tm = $("vrTabMeeting");
    var ts = $("vrTabSettings");
    var pq = $("vrPaneQuick");
    var pm = $("vrPaneMeeting");
    var ps = $("vrPaneSettings");
    if (tq) tq.classList.toggle("active", S.tab === "quick");
    if (tm) tm.classList.toggle("active", S.tab === "meeting");
    if (ts) ts.classList.toggle("active", S.tab === "settings");
    if (pq) pq.hidden = S.tab !== "quick";
    if (pm) pm.hidden = S.tab !== "meeting";
    if (ps) ps.hidden = S.tab !== "settings";
  }

  function syncQuickUi() {
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
  }

  function syncMeetingUi() {
    var mt = S.mt;
    var st = mt.statusData || {};

    var stPillsEl = $("vrStPills");
    if (stPillsEl) {
      var html = "";
      if (st.has_gpu) {
        html += '<span class="vr-pill is-ok">' + ico("cpu") + " <span>" + esc(tw("vr.meeting.hw_gpu", { name: st.gpu_name || "CUDA", vram: st.vram_mb || 0 })) + "</span></span>";
      } else {
        html += '<span class="vr-pill">' + ico("cpu") + " <span>" + esc(tw("vr.meeting.hw_cpu")) + "</span></span>";
      }
      if (!st.has_ffmpeg && st.ok) {
        html += '<span class="vr-pill is-warn">' + ico("triangle-alert") + " <span>" + esc(tw("vr.meeting.hw_no_ffmpeg")) + "</span></span>";
      }
      if (st.venv_diarize_ready) {
        html += '<span class="vr-pill is-ok">' + ico("circle-check") + " <span>" + esc(tw("vr.meeting.btn_pyannote_installed")) + "</span></span>";
      }
      stPillsEl.innerHTML = html;
    }

    var ctaState = getInstallCtaState(mt, st);

    var modelSel = $("vrMtModel");
    if (modelSel && st.models && st.models.length) {
      var curVal = mt.model || "large-v3-turbo";
      modelSel.innerHTML = st.models.map(function (m) {
        var tag = m.cached ? tw("vr.meeting.model_ready") : tw("vr.meeting.model_download", { size: m.size_hint });
        return '<option value="' + esc(m.id) + '">' + esc(m.id + " " + tag) + "</option>";
      }).join("");
      modelSel.value = curVal;
      modelSel.disabled = mt.recState !== "idle" || mt.uploading || ctaState.busy;
    }

    var langSel = $("vrMtLang");
    if (langSel) {
      langSel.value = mt.language || "vi";
      langSel.disabled = mt.recState !== "idle" || mt.uploading;
    }
    var spkSel = $("vrMtNumSpk");
    if (spkSel) {
      spkSel.value = String(mt.numSpeakers || 0);
      spkSel.disabled = mt.recState !== "idle" || mt.uploading;
    }
    var diarizeCb = $("vrMtDiarize");
    if (diarizeCb) {
      diarizeCb.checked = !!mt.diarize;
      diarizeCb.disabled = mt.recState !== "idle" || mt.uploading;
    }
    var titleEl = $("vrMtTitle");
    if (titleEl && titleEl.value !== mt.title) titleEl.value = mt.title;

    var drawer = $("vrMtDrawer");
    if (drawer) drawer.hidden = !mt.drawerOpen;

    var tokBadge = $("vrMtTokStatus");
    if (tokBadge) {
      tokBadge.hidden = !st.has_hf_token;
      tokBadge.textContent = st.has_hf_token ? tw("vr.meeting.token_saved") : "";
    }
    var clearTokBtn = $("vrMtBtnClearTok");
    if (clearTokBtn) clearTokBtn.hidden = !st.has_hf_token;

    var activeModel = mt.installPendingModel || (st.installing ? st.install_model : "") || mt.model || "large-v3-turbo";
    var instAllBtn = $("vrMtBtnInstallAll");
    if (instAllBtn) {
      instAllBtn.hidden = !ctaState.installAll.visible;
      instAllBtn.disabled = ctaState.installAll.disabled;
      instAllBtn.classList.toggle("is-done", ctaState.installAll.lockedDone);
      instAllBtn.innerHTML = ico(ctaState.installAll.lockedDone ? "circle-check" : "download") +
        " <span>" + esc(tw(ctaState.installAll.labelKey, { model: activeModel })) + "</span>";
    }
    var instModBtn = $("vrMtBtnInstallModel");
    if (instModBtn) {
      instModBtn.hidden = !ctaState.installModel.visible;
      instModBtn.disabled = ctaState.installModel.disabled;
      instModBtn.classList.toggle("is-done", ctaState.installModel.lockedDone);
      instModBtn.innerHTML = ico(ctaState.installModel.lockedDone ? "circle-check" : "download") +
        " <span>" + esc(tw(ctaState.installModel.labelKey, { model: activeModel })) + "</span>";
    }
    var redlBtn = $("vrMtBtnRedownload");
    if (redlBtn) {
      redlBtn.hidden = !ctaState.redownloadModel.visible;
      redlBtn.disabled = ctaState.redownloadModel.disabled;
      redlBtn.innerHTML = ico("refresh-cw") +
        " <span>" + esc(tw(ctaState.redownloadModel.labelKey, { model: mt.model || activeModel })) + "</span>";
    }
    var delModBtn = $("vrMtBtnDeleteModel");
    if (delModBtn) {
      delModBtn.hidden = !ctaState.deleteModel.visible;
      delModBtn.disabled = ctaState.deleteModel.disabled;
      delModBtn.innerHTML = ico("trash-2") +
        " <span>" + esc(tw(ctaState.deleteModel.labelKey, { model: mt.model || activeModel })) + "</span>";
    }
    var instNotice = $("vrMtInstallNotice");
    if (instNotice) {
      var n = mt.installNotice;
      instNotice.hidden = !(n && n.text);
      instNotice.className = "vr-mt-install-notice" + (n && n.type === "ok" ? " is-ok" : n && n.type === "err" ? " is-err" : "");
      if (n && n.text) {
        instNotice.innerHTML = ico(n.type === "ok" ? "circle-check" : "triangle-alert", { cls: n.type === "ok" ? "ic-ok" : "ic-warn" }) +
          " <span>" + esc(n.text) + "</span>";
      } else {
        instNotice.innerHTML = "";
      }
    }
    var instLog = $("vrMtInstallLog");
    if (instLog) {
      var logTxt = st.install_log || st.install_progress || st.install_error || "";
      var showLog = Boolean(logTxt && (st.installing || st.install_error || (mt.installNotice && mt.installNotice.type === "err")));
      instLog.hidden = !showLog;
      if (showLog) {
        instLog.textContent = logTxt;
        instLog.scrollTop = instLog.scrollHeight;
      }
    }

    // Yellow non-blocking warning banner when HF_TOKEN missing or diarize library not installed
    var warnEl = $("vrMtWarnBanner");
    if (warnEl) {
      var wtext = mt.warnMsg || "";
      if (!wtext && st.ok && mt.diarize) {
        if (!st.venv_diarize_ready) wtext = tw("vr.meeting.diarize_not_installed_warn");
        else if (!st.has_hf_token) wtext = tw("vr.meeting.token_missing_warn");
      }
      warnEl.hidden = !wtext;
      if (wtext) {
        warnEl.innerHTML =
          '<span class="vr-warn-txt">' + ico("triangle-alert", { cls: "ic-warn" }) + " <span>" + esc(wtext) + "</span></span>" +
          (!st.has_hf_token || !st.venv_diarize_ready
            ? '<button type="button" class="gcard-btn" id="vrMtBtnOpenSetup">' + ico("sliders-horizontal") + " <span>" + esc(tw("vr.tab.settings")) + "</span></button>"
            : "");
        var openSetup = $("vrMtBtnOpenSetup");
        if (openSetup) openSetup.onclick = function () { S.tab = "settings"; syncUi(); };
      } else {
        warnEl.innerHTML = "";
      }
    }

    var dot = $("vrMtDot");
    var stxt = $("vrMtStatusText");
    var timer = $("vrMtTimer");
    var meter = $("vrMtMeter");
    var job = mt.activeJob;
    var activeJobRunning = job && ["uploading", "queued", "converting", "transcribing", "diarizing", "merging"].indexOf(job.status) >= 0;

    if (dot) {
      dot.className = "vr-dot " + (
        mt.recState === "recording" ? "is-rec"
          : (mt.recState === "paused" || mt.uploading || activeJobRunning || mt.normalizing) ? "is-busy"
            : (job && job.status === "done") ? "is-done" : "is-idle"
      );
    }
    if (stxt) {
      if (mt.recState === "recording") stxt.textContent = tw("vr.status.recording");
      else if (mt.recState === "paused") stxt.textContent = tw("vr.meeting.status_paused");
      else if (mt.uploading) stxt.textContent = tw("vr.meeting.status_uploading", { done: mt.uploadDone, total: mt.uploadTotal || 1 });
      else if (activeJobRunning) stxt.textContent = jobStatusLabel(job);
      else if (st.installing) stxt.textContent = tw("vr.meeting.installing", { detail: st.install_progress || "..." });
      else stxt.textContent = tw("vr.status.idle");
    }
    if (timer) {
      timer.hidden = mt.recState === "idle";
      if (!timer.hidden) timer.textContent = fmtTime(mtElapsedSec());
    }
    if (meter) meter.hidden = mt.recState !== "recording";

    var btnStart = $("vrMtBtnStart");
    var btnPause = $("vrMtBtnPause");
    var btnResume = $("vrMtBtnResume");
    var btnStop = $("vrMtBtnStop");
    var btnCancel = $("vrMtBtnCancel");
    var btnUpload = $("vrMtBtnUpload");

    if (btnStart) { btnStart.hidden = mt.recState !== "idle"; btnStart.disabled = !canMeetingStart(mt); }
    if (btnPause) { btnPause.hidden = !canMeetingPause(mt); }
    if (btnResume) { btnResume.hidden = !canMeetingResume(mt); }
    if (btnStop) { btnStop.hidden = !canMeetingStop(mt); }
    if (btnCancel) { btnCancel.hidden = mt.recState === "idle"; }
    if (btnUpload) { btnUpload.disabled = mt.recState !== "idle" || mt.uploading; }

    var progBox = $("vrMtProgress");
    if (progBox) {
      var showProg = mt.uploading || activeJobRunning || (job && job.status === "failed");
      progBox.hidden = !showProg;
      if (showProg) {
        var pct = mt.uploading
          ? (mt.uploadTotal ? Math.round((mt.uploadDone / mt.uploadTotal) * 100) : 5)
          : Math.max(0, Math.min(100, Number((job && job.progress) || 0)));
        var pLab = $("vrMtProgLabel");
        var pPct = $("vrMtProgPct");
        var pFill = $("vrMtProgFill");
        if (pLab) pLab.textContent = mt.uploading ? tw("vr.meeting.status_uploading", { done: mt.uploadDone, total: mt.uploadTotal || 1 }) : jobStatusLabel(job);
        if (pPct) pPct.textContent = pct + "%";
        if (pFill) pFill.style.width = pct + "%";
      }
    }

    var errEl = $("vrMtError");
    if (errEl) {
      var errTxt = mt.errorMsg || (job && job.status === "failed" ? job.error : "");
      errEl.hidden = !errTxt;
      if (errTxt) {
        errEl.innerHTML = ico("triangle-alert", { cls: "ic-warn" }) + " <span>" + esc(errTxt) + "</span>";
      } else {
        errEl.innerHTML = "";
      }
    }
  }

  function syncEditorCard() {
    var edCard = $("vrEditorCard");
    var edFile = $("vrAuditFile");
    var edTime = $("vrAuditTime");
    var edNote = $("vrAuditFallback");
    var closedBar = $("vrClosedNotice");
    var vrEdit = $("vrEdit");

    var rec = S.activeRecord;
    if (!edCard) return;
    edCard.hidden = !rec || S.tab === "settings";
    if (edCard.hidden) return;

    if (edFile) edFile.textContent = rec.file_path || "";
    if (edTime) edTime.textContent = fmtDateLabel(rec.created_iso);

    var isMeeting = rec.mode === "meeting";
    var canRediarize = isMeeting && !!rec.has_audio;

    if (edNote) {
      var warnTxt = S.infoNote || warningTextForCodes(rec.warnings);
      edNote.hidden = !warnTxt;
      if (!edNote.hidden) {
        var isPyannoteWarn = Array.isArray(rec.warnings) && (
          rec.warnings.indexOf("pyannote_auth_warn") !== -1 ||
          rec.warnings.indexOf("missing_hf_token") !== -1 ||
          rec.warnings.indexOf("pyannote_not_installed") !== -1
        );
        if (canRediarize && isPyannoteWarn) {
          edNote.innerHTML = '<div class="vr-fallback-row">' +
            '<span>' + esc(warnTxt) + '</span>' +
            '<button type="button" class="gcard-btn vr-btn-rediarize-banner" id="vrMtBtnRediarizeBanner">' +
            ico(S.mt.rediarizing ? "loader" : "refresh-cw", { cls: S.mt.rediarizing ? "spin" : "" }) +
            ' <span>' + esc(S.mt.rediarizing ? tw("vr.meeting.btn_rediarizing") : tw("vr.meeting.btn_rediarize")) + '</span></button>' +
            '</div>';
          var btnBanner = $("vrMtBtnRediarizeBanner");
          if (btnBanner) {
            btnBanner.disabled = !!S.mt.rediarizing;
            btnBanner.onclick = function () { rediarizeMeetingRecord(rec); };
          }
        } else {
          edNote.textContent = warnTxt || "";
        }
      }
    }
    var audioWrap = $("vrMtAudioWrap");
    if (audioWrap) {
      audioWrap.hidden = !(isMeeting && rec.has_audio);
      if (!audioWrap.hidden) {
        var player = $("vrMtAudioPlayer");
        var wantSrc = "/voice-records/" + encodeURIComponent(rec.id) + "/audio";
        if (player && player.getAttribute("data-rec-id") !== rec.id) {
          player.setAttribute("data-rec-id", rec.id);
          player.src = wantSrc;
        }
        var pinBtn = $("vrMtBtnPin");
        if (pinBtn) {
          var pinned = !!rec.pinned;
          pinBtn.classList.toggle("is-pinned", pinned);
          pinBtn.innerHTML = ico(pinned ? "pin-off" : "pin") + " <span>" + esc(pinned ? tw("vr.meeting.btn_pinned") : tw("vr.meeting.btn_pin")) + "</span>";
        }
        var retTxt = $("vrMtBtnPin");
        if (retTxt) {
          var keepDays = (S.mt.statusData && S.mt.statusData.config && S.mt.statusData.config.keep_audio_days) || 30;
          retTxt.title = rec.pinned ? tw("vr.meeting.audio_pinned_note") : tw("vr.meeting.audio_retention", { days: keepDays });
        }
      }
    }

    var spkWrap = $("vrMtSpeakersWrap");
    var spkGrid = $("vrMtSpeakersGrid");
    var speakers = (rec.speakers || []);
    if (spkWrap && spkGrid) {
      spkWrap.hidden = !(isMeeting && speakers.length > 0);
      if (!spkWrap.hidden) {
        var draft = S.mt.speakerDraft || {};
        spkGrid.innerHTML = speakers.map(function (sp) {
          var val = draft[sp.id] != null ? draft[sp.id] : ((rec.speaker_map && rec.speaker_map[sp.id]) || "");
          return '<div class="vr-mt-spk-item">' +
            '<div class="vr-mt-spk-top">' +
            '<span class="vr-mt-spk-id">' + esc(sp.id) + "</span>" +
            '<span class="vr-mt-spk-stat">' + esc(tw("vr.meeting.speaker_stats", { dur: fmtTime(sp.duration), turns: sp.turns })) + "</span>" +
            "</div>" +
            '<input type="text" class="vr-input vr-mt-spk-input" data-spkid="' + esc(sp.id) + '" value="' + esc(val) + '" placeholder="' + esc(tw("vr.meeting.speaker_placeholder")) + '">' +
            "</div>";
        }).join("");
        spkGrid.querySelectorAll(".vr-mt-spk-input").forEach(function (inp) {
          inp.addEventListener("input", function () {
            S.mt.speakerDraft[inp.getAttribute("data-spkid")] = inp.value;
          });
        });
      }
    }

    var actBar = $("vrMtActionsBar");
    if (actBar) {
      actBar.hidden = !isMeeting;
      actBar.classList.toggle("has-3-cols", !canRediarize);
      var normBtn = $("vrMtBtnNormalizeRec");
      if (normBtn) {
        normBtn.disabled = !!S.mt.normalizing;
        normBtn.innerHTML = ico("sparkles") + " <span>" + esc(S.mt.normalizing ? tw("vr.btn.normalizing") : tw("vr.btn.normalize")) + "</span>";
      }
      var rediarizeBtn = $("vrMtBtnRediarizeRec");
      if (rediarizeBtn) {
        rediarizeBtn.hidden = !canRediarize;
        if (canRediarize) {
          rediarizeBtn.disabled = !!S.mt.rediarizing;
          rediarizeBtn.innerHTML = ico(S.mt.rediarizing ? "loader" : "refresh-cw", { cls: S.mt.rediarizing ? "spin" : "" }) +
            " <span>" + esc(S.mt.rediarizing ? tw("vr.meeting.btn_rediarizing") : tw("vr.meeting.btn_rediarize")) + "</span>";
        }
      }
      var exts = ["srt", "vtt", "txt", "json"];
      exts.forEach(function (ext) {
        var lnk = $("vrMtExp_" + ext);
        if (lnk) lnk.href = "/voice-records/" + encodeURIComponent(rec.id) + "/export?format=" + ext;
      });
    }

    if (closedBar) closedBar.hidden = !S.editorClosed;
    if (vrEdit) vrEdit.hidden = !!S.editorClosed;
  }

  function syncUi() {
    if (!S.host || !S.mounted) return;
    syncTabs();
    syncQuickUi();
    syncMeetingUi();
    syncEditorCard();
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
      var isMt = r.mode === "meeting";
      var snippet = String(r.raw_text || "").replace(/\s+/g, " ").trim();
      if (snippet.length > 120) snippet = snippet.slice(0, 120) + "...";
      return '<button type="button" class="vr-hist-item' + act + '" data-vrid="' + esc(r.id) + '">' +
        '<div class="vr-hist-top">' +
        '<span class="vr-hist-file">' + ico(isMt ? "file-audio" : "file-text") + " <span>" + esc(basename(r.file_path) || r.file_path) + "</span></span>" +
        (isMt ? '<span class="vr-hist-badge">' + esc(tw("vr.hist.badge_meeting") + (r.duration_sec ? " · " + fmtTime(r.duration_sec) : "")) + "</span>" : "") +
        '<span class="vr-hist-time">' + esc(fmtDateLabel(r.created_iso)) + "</span>" +
        "</div>" +
        '<div class="vr-hist-raw">' + esc(snippet) + "</div>" +
        "</button>";
    }).join("");

    list.querySelectorAll(".vr-hist-item").forEach(function (btn) {
      btn.onclick = function () {
        if (isBusy(S) || S.state === "recording" || S.mt.recState !== "idle" || S.mt.uploading) return;
        var id = btn.getAttribute("data-vrid");
        var rec = S.history.find(function (x) { return x.id === id; });
        if (rec) openRecordInEditor(rec);
      };
    });
  }

  function openRecordInEditor(rec, opts) {
    if (!rec) return;
    opts = opts || {};
    S.activeRecord = rec;
    S.errorMsg = "";
    S.editorClosed = false;
    if (rec.mode === "meeting") {
      if (opts.switchTab !== false) S.tab = "meeting";
      S.mt.speakerDraft = Object.assign({}, rec.speaker_map || {});
    } else {
      S.locked = true;
      S.rawText = String(rec.raw_text || "");
    }
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
            syncEditorCard();
          },
        }
      );
    }
  }

  // ── Quick Note Dictation ───────────────────────────────────────────

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
    } catch (e) { }

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
      onStream: function (stream) {
        startMeter(stream, "vrMeter");
      },
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
    openRecordInEditor(rec, { switchTab: false });
    refreshHistory();
  }

  function resetNewRecording() {
    if (!canNewRecord(S) || S.mt.recState !== "idle" || S.mt.uploading) return;
    try {
      if (window.JavisNoteEditor && typeof window.JavisNoteEditor.closeInSlot === "function") {
        window.JavisNoteEditor.closeInSlot();
      }
    } catch (e) { }
    S.locked = false;
    S.rawText = "";
    S.baseBeforeTake = "";
    S.takeText = "";
    S.errorMsg = "";
    S.infoNote = "";
    S.activeRecord = null;
    S.editorClosed = false;
    S.mt.errorMsg = "";
    S.mt.warnMsg = "";
    S.mt.speakerDraft = {};
    syncUi();
    var rawEl = $("vrRawInput");
    if (rawEl && S.tab === "quick") try { rawEl.focus(); } catch (e) { }
  }

  // ── Long Meeting Mode (MediaRecorder + Blob.slice chunk upload) ────

  function stopMeetingStream() {
    stopMtClock();
    stopMeter();
    if (S.mt.stream) {
      try { S.mt.stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) { }
      S.mt.stream = null;
    }
    S.mt.mr = null;
  }

  function pickAudioMime() {
    if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return "audio/webm";
    var prefs = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
    for (var i = 0; i < prefs.length; i++) {
      if (MediaRecorder.isTypeSupported(prefs[i])) return prefs[i];
    }
    return "";
  }

  function extFromMime(mime) {
    var m = String(mime || "").toLowerCase();
    if (m.indexOf("mp4") >= 0 || m.indexOf("m4a") >= 0) return ".m4a";
    if (m.indexOf("ogg") >= 0) return ".ogg";
    if (m.indexOf("wav") >= 0) return ".wav";
    return ".webm";
  }

  async function startMeetingRecording() {
    if (!canMeetingStart(S.mt)) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === "undefined") {
      S.mt.errorMsg = tw("vr.meeting.err_mic");
      syncMeetingUi();
      return;
    }

    S.mt.errorMsg = "";
    S.mt.warnMsg = "";
    S.mt.cancelled = false;
    S.mt.chunks = [];
    S.mt.segments = [];
    S.mt.elapsedMs = 0;
    S.mt.resumeAt = 0;

    try {
      var stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (!S.mounted) {
        stream.getTracks().forEach(function (t) { t.stop(); });
        return;
      }
      var mime = pickAudioMime();
      var mr = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      S.mt.stream = stream;
      S.mt.mr = mr;
      S.mt.mimeType = mr.mimeType || mime || "audio/webm";

      mr.ondataavailable = function (ev) {
        if (ev.data && ev.data.size > 0) S.mt.chunks.push(ev.data);
      };
      mr.onstop = function () {
        var segBlob = S.mt.chunks.length ? new Blob(S.mt.chunks, { type: S.mt.mimeType }) : null;
        S.mt.chunks = [];
        if (segBlob && segBlob.size > 0) S.mt.segments.push(segBlob);
        if (S.mt._segmentOnly) {
          S.mt._segmentOnly = false;
          return;
        }
        var segments = S.mt.segments.slice();
        var cancelled = S.mt.cancelled;
        S.mt.segments = [];
        stopMeetingStream();
        S.mt.recState = "idle";
        syncMeetingUi();
        if (!cancelled && segments.length && S.mounted) {
          submitMeetingSegments(segments, extFromMime(S.mt.mimeType));
        }
      };

      mr.start(1000);
      S.mt.recState = "recording";
      S.mt.resumeAt = Date.now();
      startMtClock();
      startMeter(stream, "vrMtMeter");
      syncMeetingUi();
    } catch (e) {
      stopMeetingStream();
      S.mt.recState = "idle";
      S.mt.errorMsg = tw("vr.meeting.err_mic");
      syncMeetingUi();
    }
  }

  function pauseMeetingRecording() {
    if (!canMeetingPause(S.mt) || !S.mt.mr) return;
    if (S.mt.resumeAt) {
      S.mt.elapsedMs += (Date.now() - S.mt.resumeAt);
      S.mt.resumeAt = 0;
    }
    stopMtClock();
    S.mt.recState = "paused";
    if (typeof S.mt.mr.pause === "function" && S.mt.mr.state === "recording") {
      try { S.mt.mr.pause(); } catch (e) { }
    } else {
      S.mt._segmentOnly = true;
      try { S.mt.mr.stop(); } catch (e) { }
    }
    syncMeetingUi();
  }

  function resumeMeetingRecording() {
    if (!canMeetingResume(S.mt)) return;
    if (S.mt.mr && typeof S.mt.mr.resume === "function" && S.mt.mr.state === "paused") {
      S.mt.mr.resume();
      S.mt.recState = "recording";
      S.mt.resumeAt = Date.now();
      startMtClock();
      syncMeetingUi();
    } else if (S.mt.stream) {
      var mr = S.mt.mimeType ? new MediaRecorder(S.mt.stream, { mimeType: S.mt.mimeType }) : new MediaRecorder(S.mt.stream);
      S.mt.mr = mr;
      S.mt.chunks = [];
      mr.ondataavailable = function (ev) {
        if (ev.data && ev.data.size > 0) S.mt.chunks.push(ev.data);
      };
      mr.onstop = function () {
        var segBlob = S.mt.chunks.length ? new Blob(S.mt.chunks, { type: S.mt.mimeType }) : null;
        S.mt.chunks = [];
        if (segBlob && segBlob.size > 0) S.mt.segments.push(segBlob);
        if (S.mt._segmentOnly) { S.mt._segmentOnly = false; return; }
        var segments = S.mt.segments.slice();
        var cancelled = S.mt.cancelled;
        S.mt.segments = [];
        stopMeetingStream();
        S.mt.recState = "idle";
        syncMeetingUi();
        if (!cancelled && segments.length && S.mounted) {
          submitMeetingSegments(segments, extFromMime(S.mt.mimeType));
        }
      };
      mr.start(1000);
      S.mt.recState = "recording";
      S.mt.resumeAt = Date.now();
      startMtClock();
      syncMeetingUi();
    }
  }

  function stopMeetingRecording(cancel) {
    if (!canMeetingStop(S.mt)) return;
    S.mt.cancelled = !!cancel;
    if (S.mt.resumeAt) {
      S.mt.elapsedMs += (Date.now() - S.mt.resumeAt);
      S.mt.resumeAt = 0;
    }
    if (S.mt.mr && S.mt.mr.state !== "inactive") {
      try { S.mt.mr.stop(); } catch (e) { }
    } else {
      var segments = S.mt.segments.slice();
      S.mt.segments = [];
      stopMeetingStream();
      S.mt.recState = "idle";
      syncMeetingUi();
      if (!cancel && segments.length) {
        submitMeetingSegments(segments, extFromMime(S.mt.mimeType));
      }
    }
  }

  async function uploadChunkWithRetry(jobId, idx, blobPart, retries) {
    var attempts = retries || 3;
    var lastErr = null;
    for (var i = 0; i < attempts; i++) {
      try {
        var fd = new FormData();
        fd.append("chunk_index", String(idx));
        fd.append("file", blobPart, "chunk_" + idx + ".part");
        var r = await fetch("/voice-records/meeting/jobs/" + encodeURIComponent(jobId) + "/chunk", {
          method: "POST",
          credentials: "same-origin",
          body: fd,
        });
        var d = await r.json().catch(function () { return {}; });
        if (r.ok && d && d.ok) return d;
        lastErr = new Error((d && d.error) || ("HTTP " + r.status));
      } catch (e) {
        lastErr = e;
      }
      await new Promise(function (resolve) { setTimeout(resolve, 600 * (i + 1)); });
    }
    throw lastErr || new Error(tw("vr.meeting.err_upload"));
  }

  async function submitMeetingSegments(segments, fileExt) {
    var uploadMode = segments.length > 1 ? "multi_segment" : "raw_upload";
    var parts = uploadMode === "raw_upload"
      ? sliceBlobChunks(segments[0], CHUNK_SIZE_BYTES)
      : segments;
    await runChunkedJobUpload(parts, {
      upload_mode: uploadMode,
      file_ext: fileExt || ".webm",
    });
  }

  async function submitUploadedFile(file) {
    if (!file || S.mt.recState !== "idle" || S.mt.uploading) return;
    var name = String(file.name || "meeting.webm");
    var dotIdx = name.lastIndexOf(".");
    var ext = dotIdx >= 0 ? name.slice(dotIdx).toLowerCase() : ".webm";
    var parts = sliceBlobChunks(file, CHUNK_SIZE_BYTES);
    if (!parts.length) return;
    if (!S.mt.title) {
      S.mt.title = dotIdx > 0 ? name.slice(0, dotIdx) : name;
    }
    await runChunkedJobUpload(parts, {
      upload_mode: "raw_upload",
      file_ext: ext,
    });
  }

  async function runChunkedJobUpload(parts, meta) {
    S.mt.uploading = true;
    S.mt.errorMsg = "";
    S.mt.warnMsg = "";
    S.mt.uploadDone = 0;
    S.mt.uploadTotal = parts.length;
    syncMeetingUi();

    try {
      var cr = await fetch("/voice-records/meeting/jobs", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          brain: activeBrain(),
          title: S.mt.title || "",
          upload_mode: meta.upload_mode || "raw_upload",
          file_ext: meta.file_ext || ".webm",
          model: S.mt.model || "large-v3-turbo",
          language: S.mt.language || "vi",
          diarize: !!S.mt.diarize,
          num_speakers: Number(S.mt.numSpeakers || 0),
          total_chunks: parts.length,
        }),
      });
      var cd = await cr.json().catch(function () { return {}; });
      if (!cr.ok || !cd || !cd.ok || !cd.job) {
        throw new Error((cd && cd.error) || tw("vr.meeting.err_upload"));
      }
      var job = cd.job;
      S.mt.activeJob = job;
      syncMeetingUi();

      for (var i = 0; i < parts.length; i++) {
        if (!S.mounted) return;
        await uploadChunkWithRetry(job.id, i, parts[i], 3);
        S.mt.uploadDone = i + 1;
        syncMeetingUi();
      }

      var fr = await fetch("/voice-records/meeting/jobs/" + encodeURIComponent(job.id) + "/finish", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          total_chunks: parts.length,
          upload_mode: meta.upload_mode || "raw_upload",
          file_ext: meta.file_ext || ".webm",
          title: S.mt.title || "",
        }),
      });
      var fd = await fr.json().catch(function () { return {}; });
      if (!fr.ok || !fd || !fd.ok || !fd.job) {
        throw new Error((fd && fd.error) || tw("vr.meeting.err_upload"));
      }
      S.mt.activeJob = fd.job;
      S.mt.uploading = false;
      syncMeetingUi();
      startJobPolling(fd.job.id);
    } catch (e) {
      S.mt.uploading = false;
      S.mt.errorMsg = (e && e.message) || tw("vr.meeting.err_upload");
      syncMeetingUi();
    }
  }

  function stopJobPolling() {
    if (S.mt.jobPollTimer) { clearTimeout(S.mt.jobPollTimer); S.mt.jobPollTimer = null; }
  }

  function startJobPolling(jobId) {
    stopJobPolling();
    async function poll() {
      if (!S.mounted || !jobId) return;
      try {
        var r = await fetch("/voice-records/meeting/jobs/" + encodeURIComponent(jobId), {
          cache: "no-store",
          credentials: "same-origin",
        });
        var d = await r.json().catch(function () { return {}; });
        if (!S.mounted) return;
        if (r.ok && d && d.job) {
          S.mt.activeJob = d.job;
          var st = d.job.status;
          if (st === "done") {
            S.mt.rediarizing = false;
            S.mt.warnMsg = warningTextForCodes(d.job.warnings);
            if (d.record) {
              S.history = [d.record].concat(S.history.filter(function (x) { return x.id !== d.record.id; }));
              openRecordInEditor(d.record);
            }
            refreshHistory();
            return;
          }
          if (st === "failed") {
            S.mt.rediarizing = false;
            S.mt.errorMsg = d.job.error || tw("vr.meeting.status_failed");
            syncMeetingUi();
            return;
          }
          syncMeetingUi();
        }
      } catch (e) { }
      if (S.mounted) S.mt.jobPollTimer = setTimeout(poll, 1500);
    }
    S.mt.jobPollTimer = setTimeout(poll, 900);
  }

  function stopInstallPolling() {
    if (S.mt.installPollTimer) { clearTimeout(S.mt.installPollTimer); S.mt.installPollTimer = null; }
  }

  function resolveInstallCompletion(st) {
    var action = S.mt.installPending || (st && st.install_mode) || "all";
    var targetModel = S.mt.installPendingModel || (st && st.install_model) || S.mt.model || "large-v3-turbo";
    var cachedMap = (st && st.cached_whisper_models) || {};
    var modelOk = Boolean(cachedMap[targetModel]);
    var diarizeOk = Boolean(st && st.venv_diarize_ready);

    S.mt.installPending = "";
    S.mt.installPendingModel = "";
    S.mt.pollErrCount = 0;

    if (st && (st.install_error || st.install_ok === false)) {
      var errTxt = st.install_error || tw("vr.meeting.install_incomplete");
      S.mt.installNotice = { type: "err", text: tw("vr.meeting.install_failed", { err: errTxt }) };
      return;
    }
    if (action === "all" && !(diarizeOk && modelOk)) {
      S.mt.installNotice = { type: "err", text: tw("vr.meeting.install_incomplete") };
      return;
    }
    if ((action === "model" || action === "whisper" || action === "redownload") && !modelOk) {
      S.mt.installNotice = { type: "err", text: tw("vr.meeting.install_incomplete") };
      return;
    }
    if (action === "redownload") {
      S.mt.installNotice = { type: "ok", text: tw("vr.meeting.install_success_redownload", { model: targetModel }) };
    } else if (action === "model" || action === "whisper") {
      S.mt.installNotice = { type: "ok", text: tw("vr.meeting.install_success_model", { model: targetModel }) };
    } else {
      S.mt.installNotice = { type: "ok", text: tw("vr.meeting.install_success_all", { model: targetModel }) };
    }
  }

  function startInstallPolling() {
    stopInstallPolling();
    async function poll() {
      if (!S.mounted) return;
      var st = await fetchMeetingStatus();
      if (!S.mounted) return;
      if (!st) {
        S.mt.pollErrCount = (S.mt.pollErrCount || 0) + 1;
        if (S.mt.pollErrCount >= 4) {
          S.mt.installPending = "";
          S.mt.installPendingModel = "";
          S.mt.installNotice = { type: "err", text: tw("vr.meeting.install_failed", { err: "Network error" }) };
          syncMeetingUi();
          return;
        }
        S.mt.installPollTimer = setTimeout(poll, 2000);
        return;
      }
      S.mt.pollErrCount = 0;
      if (st.installing) {
        S.mt.installPollTimer = setTimeout(poll, 1500);
      }
    }
    S.mt.installPollTimer = setTimeout(poll, 1000);
  }

  async function fetchMeetingStatus() {
    var wasInstalling = Boolean(S.mt.statusData && S.mt.statusData.installing);
    try {
      var r = await fetch("/voice-records/meeting/status", { cache: "no-store", credentials: "same-origin" });
      var d = await r.json().catch(function () { return {}; });
      if (!S.mounted || !r.ok) return null;
      S.mt.statusData = d;
      if (d.config) {
        if (S.mt.recState === "idle" && !S.mt.uploading && !S.mt.installPending && !d.installing) {
          if (d.config.whisper_model) S.mt.model = d.config.whisper_model;
          if (d.config.language) S.mt.language = d.config.language;
          if (!S.mt.diarizeTouched) {
            S.mt.diarize = d.diarize_ready ? true : Boolean(d.config.diarize);
          }
        }
      }
      if (d.installing) {
        if (!S.mt.installPollTimer) startInstallPolling();
      } else if (S.mt.installPending || wasInstalling) {
        resolveInstallCompletion(d);
        stopInstallPolling();
      }
      syncMeetingUi();
      return d;
    } catch (e) {
      return null;
    }
  }

  async function saveMeetingConfig(patch) {
    S.mt.errorMsg = "";
    try {
      var r = await fetch("/voice-records/meeting/config", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch || {}),
      });
      var d = await r.json().catch(function () { return {}; });
      if (!S.mounted) return;
      if (r.ok && d && d.ok) {
        S.mt.statusData = d;
      } else {
        S.mt.errorMsg = (d && d.error) || ("HTTP " + r.status);
      }
      syncMeetingUi();
    } catch (e) {
      if (S.mounted) {
        S.mt.errorMsg = (e && e.message) || "Network error";
        syncMeetingUi();
      }
    }
  }

  async function triggerInstall(mode) {
    if (S.mt.installPending || (S.mt.statusData && S.mt.statusData.installing)) return;
    var act = mode || "all";
    var targetModel = S.mt.model || "large-v3-turbo";
    S.mt.errorMsg = "";
    S.mt.installNotice = null;
    S.mt.installPending = act;
    S.mt.installPendingModel = targetModel;
    S.mt.pollErrCount = 0;
    syncMeetingUi();
    try {
      var r = await fetch("/voice-records/meeting/install", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: act,
          model: targetModel,
          force: act === "redownload",
        }),
      });
      var d = await r.json().catch(function () { return {}; });
      if (!S.mounted) return;
      if (d && d.status) S.mt.statusData = d.status;
      if (!r.ok || !d || d.ok === false) {
        var msg = (d && d.error) || ("HTTP " + r.status);
        S.mt.installPending = "";
        S.mt.installPendingModel = "";
        S.mt.installNotice = { type: "err", text: tw("vr.meeting.install_failed", { err: msg }) };
        S.mt.errorMsg = msg;
        syncMeetingUi();
        return;
      }
      if (d && d.status && !d.status.installing && d.status.install_ok === false && d.status.install_error) {
        resolveInstallCompletion(d.status);
        syncMeetingUi();
        return;
      }
      syncMeetingUi();
      startInstallPolling();
    } catch (e) {
      if (S.mounted) {
        var em = (e && e.message) || "Network error";
        S.mt.installPending = "";
        S.mt.installPendingModel = "";
        S.mt.installNotice = { type: "err", text: tw("vr.meeting.install_failed", { err: em }) };
        S.mt.errorMsg = em;
        syncMeetingUi();
      }
    }
  }

  async function deleteSelectedModel() {
    if (S.mt.installPending || S.mt.deletingModel || (S.mt.statusData && S.mt.statusData.installing)) return;
    var targetModel = S.mt.model || "large-v3-turbo";
    if (typeof window !== "undefined" && window.confirm) {
      var msg = tw("vr.meeting.confirm_delete_model", { model: targetModel });
      if (!window.confirm(msg)) return;
    }
    S.mt.errorMsg = "";
    S.mt.installNotice = null;
    S.mt.deletingModel = true;
    syncMeetingUi();
    try {
      var r = await fetch("/voice-records/meeting/models/delete", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: targetModel }),
      });
      var d = await r.json().catch(function () { return {}; });
      if (!S.mounted) return;
      S.mt.deletingModel = false;
      if (d && d.status) S.mt.statusData = d.status;
      if (!r.ok || !d || d.ok === false) {
        var msg = (d && d.error) || ("HTTP " + r.status);
        S.mt.installNotice = { type: "err", text: tw("vr.meeting.delete_model_failed", { err: msg }) };
        S.mt.errorMsg = msg;
        syncMeetingUi();
        return;
      }
      S.mt.installNotice = { type: "ok", text: tw("vr.meeting.delete_model_success", { model: targetModel }) };
      syncMeetingUi();
    } catch (e) {
      if (S.mounted) {
        var em = (e && e.message) || "Network error";
        S.mt.deletingModel = false;
        S.mt.installNotice = { type: "err", text: tw("vr.meeting.delete_model_failed", { err: em }) };
        S.mt.errorMsg = em;
        syncMeetingUi();
      }
    }
  }

  async function applySpeakerMap() {
    var rec = S.activeRecord;
    if (!rec || !rec.speakers || !rec.speakers.length) return;
    S.mt.errorMsg = "";
    var smap = buildSpeakerMapPayload(rec.speakers, S.mt.speakerDraft);
    try {
      var r = await fetch("/voice-records/" + encodeURIComponent(rec.id) + "/speakers", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ speaker_map: smap, title: S.mt.title || "" }),
      });
      var d = await r.json().catch(function () { return {}; });
      if (!S.mounted) return;
      if (r.ok && d && d.ok && d.record) {
        S.activeRecord = d.record;
        S.history = S.history.map(function (x) { return x.id === d.record.id ? d.record : x; });
        openRecordInEditor(d.record, { switchTab: false });
      } else {
        S.mt.errorMsg = (d && d.error) || ("HTTP " + r.status);
        syncMeetingUi();
      }
    } catch (e) {
      if (S.mounted) {
        S.mt.errorMsg = (e && e.message) || "Network error";
        syncMeetingUi();
      }
    }
  }

  async function normalizeMeetingRecord() {
    var rec = S.activeRecord;
    if (!rec || S.mt.normalizing) return;
    S.mt.normalizing = true;
    S.mt.errorMsg = "";
    S.infoNote = "";
    syncUi();
    try {
      var r = await fetch("/voice-records/" + encodeURIComponent(rec.id) + "/normalize", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: S.mt.title || "" }),
      });
      var d = await r.json().catch(function () { return {}; });
      if (!S.mounted) return;
      S.mt.normalizing = false;
      if (r.ok && d && d.ok && d.record) {
        if (!d.ai) S.infoNote = tw("vr.audit.fallback_note");
        S.activeRecord = d.record;
        S.history = S.history.map(function (x) { return x.id === d.record.id ? d.record : x; });
        openRecordInEditor(d.record, { switchTab: false });
      } else {
        S.mt.errorMsg = (d && d.error) || tw("vr.err.normalize");
        syncUi();
      }
    } catch (e) {
      if (S.mounted) {
        S.mt.normalizing = false;
        S.mt.errorMsg = (e && e.message) || tw("vr.err.normalize");
        syncUi();
      }
    }
  }

  async function rediarizeMeetingRecord(targetRec) {
    var rec = targetRec || S.activeRecord;
    if (!rec || rec.mode !== "meeting" || !rec.has_audio || S.mt.rediarizing) return;
    S.mt.rediarizing = true;
    S.mt.errorMsg = "";
    S.mt.warnMsg = "";
    S.infoNote = "";
    syncUi();
    try {
      var r = await fetch("/voice-records/" + encodeURIComponent(rec.id) + "/rediarize", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: S.mt.model || (rec.model_used || "large-v3-turbo"),
          language: S.mt.language || "vi",
          num_speakers: Number(S.mt.numSpeakers || 0),
          title: S.mt.title || "",
        }),
      });
      var d = await r.json().catch(function () { return {}; });
      if (!S.mounted) return;
      if (r.ok && d && d.ok && d.job) {
        S.mt.activeJob = d.job;
        startJobPolling(d.job.id);
        syncMeetingUi();
        var progBox = $("vrMtProgress");
        if (progBox) progBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } else {
        S.mt.rediarizing = false;
        S.mt.errorMsg = (d && d.error) || ("HTTP " + r.status);
        syncUi();
      }
    } catch (e) {
      if (S.mounted) {
        S.mt.rediarizing = false;
        S.mt.errorMsg = (e && e.message) || "Network error";
        syncUi();
      }
    }
  }

  async function toggleRecordPin() {
    var rec = S.activeRecord;
    if (!rec) return;
    S.mt.errorMsg = "";
    try {
      var r = await fetch("/voice-records/" + encodeURIComponent(rec.id) + "/pin", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pinned: !rec.pinned }),
      });
      var d = await r.json().catch(function () { return {}; });
      if (!S.mounted) return;
      if (r.ok && d && d.ok && d.record) {
        S.activeRecord = d.record;
        S.history = S.history.map(function (x) { return x.id === d.record.id ? d.record : x; });
        syncEditorCard();
      } else {
        S.mt.errorMsg = (d && d.error) || ("HTTP " + r.status);
        syncMeetingUi();
      }
    } catch (e) {
      if (S.mounted) {
        S.mt.errorMsg = (e && e.message) || "Network error";
        syncMeetingUi();
      }
    }
  }

  async function resumeActiveJobsIfAny() {
    try {
      var r = await fetch("/voice-records/meeting/jobs?brain=" + encodeURIComponent(activeBrain()) + "&limit=5", {
        cache: "no-store",
        credentials: "same-origin",
      });
      var d = await r.json().catch(function () { return {}; });
      if (!S.mounted || !r.ok || !Array.isArray(d.jobs)) return;
      var active = d.jobs.find(function (j) {
        return ["queued", "converting", "transcribing", "diarizing", "merging"].indexOf(j.status) >= 0;
      });
      if (active) {
        S.mt.activeJob = active;
        syncMeetingUi();
        startJobPolling(active.id);
      }
    } catch (e) { }
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
    } finally {
      S.historyLoading = false;
      if (S.mounted) renderHistory();
    }
  }

  function onBeforeUnload(e) {
    if (S.state === "recording" || S.mt.recState !== "idle" || S.mt.uploading) {
      e.preventDefault();
      e.returnValue = "";
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
    S.mt.recState = "idle";
    S.mt.uploading = false;
    S.mt.normalizing = false;
    S.mt.activeJob = null;
    S.mt.installPending = "";
    S.mt.installPendingModel = "";
    S.mt.installNotice = null;
    S.mt.pollErrCount = 0;
    S.mt.errorMsg = "";
    S.mt.warnMsg = "";
    S.mt.speakerDraft = {};
    S.mt.diarizeTouched = false;

    el.innerHTML =
      '<div class="vr-page">' +
      '<div class="vr-tabs" role="tablist">' +
      '<button type="button" class="vr-tab active" id="vrTabQuick" role="tab">' + ico("mic") + " <span>" + esc(tw("vr.tab.quick")) + "</span></button>" +
      '<button type="button" class="vr-tab" id="vrTabMeeting" role="tab">' + ico("users") + " <span>" + esc(tw("vr.tab.meeting")) + "</span></button>" +
      '<button type="button" class="vr-tab" id="vrTabSettings" role="tab">' + ico("sliders-horizontal") + " <span>" + esc(tw("vr.tab.settings")) + "</span></button>" +
      "</div>" +
      '<div class="vr-grid">' +
      '<div class="vr-main">' +
      // Quick Note Pane
      '<div class="vr-pane" id="vrPaneQuick">' +
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
      '<button type="button" class="vr-btn vr-btn-ghost gcard-btn ghost" id="vrBtnCancelTake" hidden>' +
      ico("x") + " <span>" + esc(tw("vr.btn.cancel_take")) + "</span>" +
      "</button>" +
      '<button type="button" class="vr-btn vr-btn-norm" id="vrBtnNormalize" disabled>' +
      ico("sparkles") + " <span>" + esc(tw("vr.btn.normalize")) + "</span>" +
      "</button>" +
      '<button type="button" class="vr-btn gcard-btn" id="vrBtnNew">' +
      ico("plus") + " <span>" + esc(tw("vr.btn.new")) + "</span>" +
      "</button>" +
      "</div>" +
      "</div>" +
      '<div class="vr-error" id="vrError" hidden></div>' +
      '<div class="vr-lock-note" id="vrLockNote" hidden>' +
      ico("lock") + " <span>" + esc(tw("vr.raw.locked_note")) + "</span>" +
      "</div>" +
      "</div>" +
      '<div class="gcard vr-raw-card" style="margin-top:14px">' +
      '<div class="vr-card-head">' +
      '<div class="vr-card-title">' + esc(tw("vr.raw.title")) + "</div>" +
      '<div class="vr-card-meta">' +
      '<span class="vr-live-hint" id="vrLiveHint" hidden>' + esc(tw("vr.raw.live")) + "</span>" +
      '<span class="vr-badge-lock" id="vrLockBadge" hidden>' + ico("lock") + " " + esc(tw("vr.raw.locked_badge")) + "</span>" +
      "</div>" +
      "</div>" +
      '<textarea id="vrRawInput" class="vr-raw-input" rows="7" data-esc placeholder="' + esc(tw("vr.raw.placeholder")) + '"></textarea>' +
      "</div>" +
      "</div>" +
      // Long Meeting Pane
      '<div class="vr-pane" id="vrPaneMeeting" hidden>' +
      '<div class="gcard vr-controls-card">' +
      '<div class="vr-mt-form">' +
      '<div class="vr-field">' +
      "<label>" + esc(tw("vr.tab.meeting")) + "</label>" +
      '<input type="text" class="vr-input" id="vrMtTitle" placeholder="' + esc(tw("vr.meeting.title_placeholder")) + '">' +
      "</div>" +
      '<div class="vr-field">' +
      "<label>" + esc(tw("vr.meeting.lang_label")) + "</label>" +
      '<select class="vr-select" id="vrMtLang">' +
      '<option value="vi">' + esc(tw("vr.meeting.lang_vi")) + "</option>" +
      '<option value="auto">' + esc(tw("vr.meeting.lang_auto")) + "</option>" +
      '<option value="en">' + esc(tw("vr.meeting.lang_en")) + "</option>" +
      "</select>" +
      "</div>" +
      '<div class="vr-field">' +
      "<label>" + esc(tw("vr.meeting.speakers_label")) + "</label>" +
      '<select class="vr-select" id="vrMtNumSpk">' +
      '<option value="0">' + esc(tw("vr.meeting.speakers_auto")) + "</option>" +
      '<option value="2">2</option><option value="3">3</option><option value="4">4</option><option value="5">5</option><option value="6">6</option>' +
      "</select>" +
      "</div>" +
      '<label class="vr-check-wrap">' +
      '<input type="checkbox" id="vrMtDiarize" checked> <span>' + esc(tw("vr.meeting.diarize_toggle")) + "</span>" +
      "</label>" +
      "</div>" +
      '<div class="vr-bar-top">' +
      '<div class="vr-status">' +
      '<span class="vr-dot is-idle" id="vrMtDot" aria-hidden="true"></span>' +
      '<span class="vr-status-text" id="vrMtStatusText"></span>' +
      '<span class="vr-timer" id="vrMtTimer" hidden>00:00</span>' +
      '<canvas class="vr-meter" id="vrMtMeter" width="120" height="26" hidden></canvas>' +
      "</div>" +
      '<div class="vr-ctas">' +
      '<button type="button" class="vr-btn vr-btn-rec" id="vrMtBtnStart">' +
      ico("mic") + " <span>" + esc(tw("vr.meeting.btn_record")) + "</span>" +
      "</button>" +
      '<button type="button" class="vr-btn" id="vrMtBtnPause" hidden>' +
      ico("pause") + " <span>" + esc(tw("vr.meeting.btn_pause")) + "</span>" +
      "</button>" +
      '<button type="button" class="vr-btn vr-btn-rec" id="vrMtBtnResume" hidden>' +
      ico("play") + " <span>" + esc(tw("vr.meeting.btn_resume")) + "</span>" +
      "</button>" +
      '<button type="button" class="vr-btn vr-btn-norm" id="vrMtBtnStop" hidden>' +
      ico("square") + " <span>" + esc(tw("vr.meeting.btn_stop")) + "</span>" +
      "</button>" +
      '<button type="button" class="vr-btn vr-btn-ghost gcard-btn ghost" id="vrMtBtnCancel" hidden>' +
      ico("x") + " <span>" + esc(tw("vr.meeting.btn_cancel")) + "</span>" +
      "</button>" +
      '<button type="button" class="vr-btn" id="vrMtBtnUpload">' +
      ico("upload-cloud") + " <span>" + esc(tw("vr.meeting.btn_upload")) + "</span>" +
      "</button>" +
      '<input type="file" id="vrMtFileInput" accept="audio/*,video/mp4,video/webm,.mp3,.wav,.m4a,.webm,.ogg,.opus,.flac,.mp4" hidden>' +
      "</div>" +
      "</div>" +
      '<div class="vr-warn-banner" id="vrMtWarnBanner" hidden></div>' +
      '<div class="vr-mt-progress" id="vrMtProgress" hidden>' +
      '<div class="vr-mt-prog-top">' +
      '<span id="vrMtProgLabel"></span>' +
      '<span id="vrMtProgPct">0%</span>' +
      "</div>" +
      '<div class="vr-mt-prog-track"><div class="vr-mt-prog-fill" id="vrMtProgFill" style="width:0%"></div></div>' +
      "</div>" +
      '<div class="vr-error" id="vrMtError" hidden></div>' +
      "</div>" +
      "</div>" +
      // Settings Pane
      '<div class="vr-pane" id="vrPaneSettings" hidden>' +
      '<div class="gcard vr-settings-card">' +
      '<div class="vr-card-head">' +
      '<div class="vr-card-title">' + ico("sliders-horizontal") + " <span>" + esc(tw("vr.tab.settings")) + "</span></div>" +
      '<div class="vr-mt-pills" id="vrStPills"></div>' +
      "</div>" +
      '<div class="vr-settings-body">' +
      '<div class="vr-settings-sec">' +
      '<div class="vr-settings-sec-head">' +
      '<div class="vr-settings-sec-title">' + ico("key") + " <span>" + esc(tw("vr.meeting.token_section_title")) + "</span></div>" +
      '<span class="vr-pill is-ok" id="vrMtTokStatus" hidden></span>' +
      "</div>" +
      '<div class="vr-audit-sub" style="margin:4px 0 10px 0">' + renderSetupHintHtml() + "</div>" +
      '<div class="vr-mt-token-row">' +
      '<input type="password" class="vr-input" id="vrMtTokenInput" autocomplete="off" placeholder="' + esc(tw("vr.meeting.token_placeholder")) + '">' +
      '<button type="button" class="vr-btn vr-btn-norm" id="vrMtBtnSaveTok">' + ico("key") + " <span>" + esc(tw("vr.meeting.btn_save_token")) + "</span></button>" +
      '<button type="button" class="vr-btn vr-btn-ghost gcard-btn ghost" id="vrMtBtnClearTok" hidden>' + ico("trash-2") + " <span>" + esc(tw("vr.meeting.btn_clear_token")) + "</span></button>" +
      "</div>" +
      "</div>" +
      '<div class="vr-settings-sec" style="margin-top:20px; padding-top:16px; border-top:1px solid var(--border)">' +
      '<div class="vr-settings-sec-head" style="margin-bottom:12px">' +
      '<div class="vr-settings-sec-title">' + ico("cpu") + " <span>" + esc(tw("vr.meeting.model_section_title")) + "</span></div>" +
      "</div>" +
      '<div class="vr-mt-model-row">' +
      '<div class="vr-field vr-field-model">' +
      "<label>" + esc(tw("vr.meeting.model_label")) + "</label>" +
      '<select class="vr-select" id="vrMtModel"></select>' +
      "</div>" +
      '<button type="button" class="vr-btn" id="vrMtBtnInstallAll" hidden></button>' +
      '<button type="button" class="gcard-btn" id="vrMtBtnInstallModel"></button>' +
      '<button type="button" class="gcard-btn ghost" id="vrMtBtnRedownload" hidden></button>' +
      '<button type="button" class="vr-btn vr-btn-ghost gcard-btn ghost" id="vrMtBtnDeleteModel" hidden></button>' +
      "</div>" +
      '<div class="vr-mt-install-notice" id="vrMtInstallNotice" hidden></div>' +
      '<div class="vr-mt-install-log" id="vrMtInstallLog" hidden></div>' +
      "</div>" +
      "</div>" +
      "</div>" +
      "</div>" +
      // Shared Editor + Meeting Post-Processing Card
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
      '<div class="vr-mt-audio-wrap" id="vrMtAudioWrap" hidden>' +
      '<audio class="vr-mt-audio-player" id="vrMtAudioPlayer" controls preload="metadata"></audio>' +
      '<div class="vr-mt-audio-meta">' +
      '<span id="vrMtRetention"></span>' +
      '<button type="button" class="gcard-btn vr-btn-pin" id="vrMtBtnPin"></button>' +
      "</div>" +
      "</div>" +
      '<div class="vr-mt-speakers-wrap" id="vrMtSpeakersWrap" hidden>' +
      '<div class="vr-card-head" style="margin-bottom:4px">' +
      '<div class="vr-card-title">' + ico("users") + " <span>" + esc(tw("vr.meeting.speakers_panel_title")) + "</span></div>" +
      '<button type="button" class="vr-btn vr-btn-norm" id="vrMtBtnApplySpk">' + ico("circle-check") + " <span>" + esc(tw("vr.meeting.btn_apply_speakers")) + "</span></button>" +
      "</div>" +
      '<div class="vr-mt-spk-grid" id="vrMtSpeakersGrid"></div>' +
      "</div>" +
      '<div class="vr-mt-actions-bar" id="vrMtActionsBar" hidden>' +
      '<button type="button" class="vr-btn vr-btn-norm vr-act-btn" id="vrMtBtnNormalizeRec"></button>' +
      '<button type="button" class="vr-btn vr-act-btn vr-btn-rediarize" id="vrMtBtnRediarizeRec" hidden></button>' +
      '<button type="button" class="vr-btn vr-act-btn" id="vrMtBtnNewRec">' + ico("plus") + " <span>" + esc(tw("vr.btn.new")) + "</span></button>" +
      '<div class="vr-btn-group" id="vrMtExpGroup" role="group" aria-label="' + esc(tw("vr.meeting.export_label")) + '">' +
      '<a class="vr-btn-group-item is-primary" id="vrMtExp_txt" download title="' + esc(tw("vr.meeting.export_label")) + ' TXT">' + ico("download") + " TXT</a>" +
      '<a class="vr-btn-group-item" id="vrMtExp_srt" download title="' + esc(tw("vr.meeting.export_label")) + ' SRT">SRT</a>' +
      '<a class="vr-btn-group-item" id="vrMtExp_vtt" download title="' + esc(tw("vr.meeting.export_label")) + ' VTT">VTT</a>' +
      '<a class="vr-btn-group-item" id="vrMtExp_json" download title="' + esc(tw("vr.meeting.export_label")) + ' JSON">JSON</a>' +
      "</div>" +
      "</div>" +
      '<div class="vr-closed-notice" id="vrClosedNotice" hidden>' +
      "<span>" + esc(tw("vr.editor.closed")) + "</span>" +
      '<button type="button" class="gcard-btn" id="vrBtnReopen">' + ico("file-text") + " " + esc(tw("vr.btn.open_editor")) + "</button>" +
      "</div>" +
      '<div class="vr-edit" id="vrEdit" data-ne-host="1"></div>' +
      "</div>" +
      "</div>" +
      '<aside class="gcard vr-history-card">' +
      '<div class="vr-card-head">' +
      '<div class="vr-card-title">' + ico("clock") + ' <span>' + esc(tw("vr.history.title")) + "</span></div>" +
      '<span class="vr-hist-count" id="vrHistoryCount"></span>' +
      "</div>" +
      '<div class="vr-hist-list" id="vrHistoryList"></div>' +
      "</aside>" +
      "</div>" +
      "</div>";

    // Tab switch handlers
    var tabQ = $("vrTabQuick");
    var tabM = $("vrTabMeeting");
    var tabS = $("vrTabSettings");
    if (tabQ) tabQ.onclick = function () { S.tab = "quick"; syncUi(); };
    if (tabM) tabM.onclick = function () { S.tab = "meeting"; syncUi(); };
    if (tabS) tabS.onclick = function () { S.tab = "settings"; syncUi(); };

    // Quick Note handlers
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
    if (reopenBtn) reopenBtn.onclick = function () { if (S.activeRecord) openRecordInEditor(S.activeRecord, { switchTab: false }); };
    if (rawEl) {
      rawEl.addEventListener("input", function () {
        if (S.locked || S.state !== "idle") return;
        S.rawText = rawEl.value;
        var nb = $("vrBtnNormalize");
        if (nb) nb.disabled = !canNormalize(S);
      });
    }

    // Meeting handlers
    var mtCfgBtn = $("vrMtBtnConfig");
    var mtTitle = $("vrMtTitle");
    var mtModel = $("vrMtModel");
    var mtLang = $("vrMtLang");
    var mtNumSpk = $("vrMtNumSpk");
    var mtDiarize = $("vrMtDiarize");
    var mtStart = $("vrMtBtnStart");
    var mtPause = $("vrMtBtnPause");
    var mtResume = $("vrMtBtnResume");
    var mtStop = $("vrMtBtnStop");
    var mtCancel = $("vrMtBtnCancel");
    var mtUploadBtn = $("vrMtBtnUpload");
    var mtFileIn = $("vrMtFileInput");
    var mtSaveTok = $("vrMtBtnSaveTok");
    var mtClearTok = $("vrMtBtnClearTok");
    var mtInstAll = $("vrMtBtnInstallAll");
    var mtInstMod = $("vrMtBtnInstallModel");
    var mtRedlMod = $("vrMtBtnRedownload");
    var mtApplySpk = $("vrMtBtnApplySpk");
    var mtNormRec = $("vrMtBtnNormalizeRec");
    var mtRediarizeRec = $("vrMtBtnRediarizeRec");
    var mtNewRec = $("vrMtBtnNewRec");
    var mtPin = $("vrMtBtnPin");

    var mtGoSettings = $("vrMtBtnGoSettings") || $("vrMtBtnConfig");
    if (mtGoSettings) mtGoSettings.onclick = function () { S.tab = "settings"; syncUi(); };
    if (mtTitle) mtTitle.addEventListener("input", function () { S.mt.title = mtTitle.value; });
    if (mtModel) mtModel.onchange = function () { S.mt.model = mtModel.value; S.mt.installNotice = null; saveMeetingConfig({ whisper_model: S.mt.model }); syncMeetingUi(); };
    if (mtLang) mtLang.onchange = function () { S.mt.language = mtLang.value; saveMeetingConfig({ language: S.mt.language }); };
    if (mtNumSpk) mtNumSpk.onchange = function () { S.mt.numSpeakers = Number(mtNumSpk.value || 0); };
    if (mtDiarize) mtDiarize.onchange = function () { S.mt.diarize = !!mtDiarize.checked; S.mt.diarizeTouched = true; saveMeetingConfig({ diarize: S.mt.diarize }); syncMeetingUi(); };

    if (mtStart) mtStart.onclick = startMeetingRecording;
    if (mtPause) mtPause.onclick = pauseMeetingRecording;
    if (mtResume) mtResume.onclick = resumeMeetingRecording;
    if (mtStop) mtStop.onclick = function () { stopMeetingRecording(false); };
    if (mtCancel) mtCancel.onclick = function () { stopMeetingRecording(true); };
    if (mtUploadBtn && mtFileIn) {
      mtUploadBtn.onclick = function () { mtFileIn.value = ""; mtFileIn.click(); };
      mtFileIn.onchange = function () {
        var f = mtFileIn.files && mtFileIn.files[0];
        if (f) submitUploadedFile(f);
      };
    }

    if (mtSaveTok) {
      mtSaveTok.onclick = function () {
        var inp = $("vrMtTokenInput");
        var tok = inp ? inp.value.trim() : "";
        if (!tok) return;
        if (inp) inp.value = "";
        saveMeetingConfig({ hf_token: tok });
      };
    }
    if (mtClearTok) mtClearTok.onclick = function () { saveMeetingConfig({ clear_hf_token: true }); };
    if (mtInstAll) mtInstAll.onclick = function () { triggerInstall("all"); };
    if (mtInstMod) mtInstMod.onclick = function () { triggerInstall("model"); };
    if (mtRedlMod) mtRedlMod.onclick = function () { triggerInstall("redownload"); };
    var mtDelMod = $("vrMtBtnDeleteModel");
    if (mtDelMod) mtDelMod.onclick = function () { deleteSelectedModel(); };

    if (mtApplySpk) mtApplySpk.onclick = applySpeakerMap;
    if (mtNormRec) mtNormRec.onclick = normalizeMeetingRecord;
    if (mtRediarizeRec) mtRediarizeRec.onclick = function () { rediarizeMeetingRecord(); };
    if (mtNewRec) mtNewRec.onclick = resetNewRecording;
    if (mtPin) mtPin.onclick = toggleRecordPin;

    window.addEventListener("beforeunload", onBeforeUnload);

    syncUi();
    refreshHistory();
    fetchMeetingStatus();
    resumeActiveJobsIfAny();
  }

  function unmount() {
    S.mounted = false;
    window.removeEventListener("beforeunload", onBeforeUnload);
    stopClock();
    stopMtClock();
    stopMeter();
    stopJobPolling();
    stopInstallPolling();
    if (S.dict) {
      try { S.dict.cancel(); } catch (e) { }
      S.dict = null;
    }
    if (S.mt.mr && S.mt.mr.state !== "inactive") {
      S.mt.cancelled = true;
      try { S.mt.mr.stop(); } catch (e) { }
    }
    stopMeetingStream();
    try {
      if (window.JavisNoteEditor && typeof window.JavisNoteEditor.closeInSlot === "function") {
        window.JavisNoteEditor.closeInSlot();
      }
    } catch (e) { }
    S.state = "idle";
    S.mt.recState = "idle";
    S.host = null;
  }

  window.JavisVoiceRecord = Object.assign({ mount: mount, unmount: unmount }, pure);
})();
