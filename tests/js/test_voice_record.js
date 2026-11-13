/* Unit & wiring tests for the Record ("Ghi am") page (dashboard/voice-record.js).

       node tests/js/test_voice_record.js
*/
const fs = require("fs");
const path = require("path");
const ROOT = path.join(__dirname, "../..");

const VR = require(path.join(ROOT, "dashboard/voice-record.js"));
const consoleJs = fs.readFileSync(path.join(ROOT, "dashboard/console.js"), "utf8");
const consoleCss = fs.readFileSync(path.join(ROOT, "dashboard/console.css"), "utf8");
const indexHtml = fs.readFileSync(path.join(ROOT, "dashboard/index.html"), "utf8");
const viJson = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard/i18n/vi.json"), "utf8"));
const enJson = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard/i18n/en.json"), "utf8"));

const fails = [];
function check(name, cond) {
  console.log((cond ? "ok   " : "FAIL ") + name);
  if (!cond) fails.push(name);
}

// 1. Timer formatting
check("fmtTime 0 -> 00:00", VR.fmtTime(0) === "00:00");
check("fmtTime 65 -> 01:05", VR.fmtTime(65) === "01:05");

// 2. Join raw text across takes
check("joinRawText into empty returns spoken", VR.joinRawText("", "xin chào") === "xin chào");
check("joinRawText appends with space across takes", VR.joinRawText("đoạn một", "đoạn hai") === "đoạn một đoạn hai");
check("joinRawText respects trailing newline", VR.joinRawText("dòng một\n", "dòng hai") === "dòng một\ndòng hai");

// 3. CTA state machine rules
check("empty idle: canRecord=true, canNormalize=false, canCancelTake=false",
  VR.canRecord({ state: "idle", locked: false, rawText: "" }) === true
  && VR.canNormalize({ state: "idle", locked: false, rawText: "" }) === false
  && VR.canCancelTake({ state: "idle", locked: false, rawText: "" }) === false);

check("while recording: canNormalize=false, canCancelTake=true, canNewRecord=false",
  VR.canNormalize({ state: "recording", locked: false, rawText: "abc" }) === false
  && VR.canCancelTake({ state: "recording", locked: false, rawText: "abc" }) === true
  && VR.canNewRecord({ state: "recording", locked: false }) === false);

check("after stopping with rawText: canNormalize=true and canRecord=true (append before normalize)",
  VR.canNormalize({ state: "idle", locked: false, rawText: "có nội dung" }) === true
  && VR.canRecord({ state: "idle", locked: false, rawText: "có nội dung" }) === true);

check("once normalized (locked=true): canRecord=false, canNormalize=false, canNewRecord=true",
  VR.canRecord({ state: "idle", locked: true, rawText: "có nội dung" }) === false
  && VR.canNormalize({ state: "idle", locked: true, rawText: "có nội dung" }) === false
  && VR.canNewRecord({ state: "idle", locked: true }) === true);

// 4. Sidebar placement: RAIL_TOP = ["chat", "record"]
const topMatch = consoleJs.match(/const RAIL_TOP = \[([^\]]+)\]/);
const topItems = topMatch ? topMatch[1].match(/"([a-z]+)"/g).map(s => s.replace(/"/g, "")) : [];
check("RAIL_TOP places record immediately below chat",
  JSON.stringify(topItems) === JSON.stringify(["chat", "record"]));
check("VIEW_ICON assigns mic icon to record", /record:\s*"mic"/.test(consoleJs));
check("console.js exposes JavisNoteEditor.openInSlot & closeInSlot",
  /openInSlot:\s*function/.test(consoleJs) && /closeInSlot:\s*function/.test(consoleJs));
check("openNote borrows #noteEditor into #vrEdit on body.on-record",
  /body\.classList\.contains\("on-record"\)/.test(consoleJs) && /vrEdit/.test(consoleJs));

// 5. HTML script load order
const vrIdx = indexHtml.indexOf("/static/voice-record.js");
const csIdx = indexHtml.indexOf("/static/console.js");
check("index.html loads voice-record.js before console.js", vrIdx > 0 && csIdx > vrIdx);

// 6. i18n keys present in both vi.json and en.json
const requiredKeys = [
  "page.record.label", "page.record.sub",
  "vr.btn.record", "vr.btn.stop", "vr.btn.cancel_take", "vr.btn.normalize", "vr.btn.new",
  "vr.raw.title", "vr.raw.placeholder", "vr.raw.locked_badge", "vr.raw.locked_note",
  "vr.audit.title", "vr.audit.saved", "vr.history.title", "vr.history.empty"
];
check("vi.json and en.json have all record/vr keys",
  requiredKeys.every(k => typeof viJson[k] === "string" && typeof enJson[k] === "string"));

// 7. No em dash (U+2014) anywhere in new JS/CSS/i18n strings
const vrJsSrc = fs.readFileSync(path.join(ROOT, "dashboard/voice-record.js"), "utf8");
check("no U+2014 em dash in voice-record.js or console.css",
  !vrJsSrc.includes("\u2014") && !consoleCss.includes("\u2014"));

// 8. Meeting mode pure helpers, 8MB Blob.slice chunking, HH:MM:SS timer, and speaker map
check("fmtTime >= 1h formats HH:MM:SS", VR.fmtTime(3665) === "01:01:05");
const fakeBlob = {
  size: 17 * 1024 * 1024,
  slice: (a, b) => ({ start: a, end: b, size: b - a }),
};
const slices = VR.sliceBlobChunks(fakeBlob, VR.CHUNK_SIZE_BYTES);
check("sliceBlobChunks splits 17MB into 3 slices (8+8+1 MB)",
  slices.length === 3 && slices[0].size === 8 * 1024 * 1024 && slices[2].size === 1 * 1024 * 1024);

check("meeting state transitions (idle / recording / paused)",
  VR.canMeetingStart({ recState: "idle", uploading: false }) === true
  && VR.canMeetingPause({ recState: "recording" }) === true
  && VR.canMeetingResume({ recState: "paused" }) === true
  && VR.canMeetingStop({ recState: "paused" }) === true
  && VR.canMeetingStop({ recState: "idle" }) === false);

const spkPayload = VR.buildSpeakerMapPayload(
  [{ id: "SPEAKER_00" }, { id: "SPEAKER_01" }, { id: "SPEAKER_02" }],
  { SPEAKER_00: "Anh Nam", SPEAKER_01: "SPEAKER_01", SPEAKER_02: "  " }
);
check("buildSpeakerMapPayload filters blank/unchanged names",
  JSON.stringify(spkPayload) === JSON.stringify({ SPEAKER_00: "Anh Nam" }));

const meetingKeys = [
  "vr.tab.quick", "vr.tab.meeting", "vr.tab.settings", "vr.meeting.btn_record", "vr.meeting.btn_pause",
  "vr.meeting.btn_resume", "vr.meeting.btn_stop", "vr.meeting.btn_upload",
  "vr.meeting.token_missing_warn", "vr.meeting.btn_apply_speakers", "vr.meeting.btn_pin",
  "vr.meeting.btn_rediarize", "vr.meeting.btn_rediarizing",
  "vr.meeting.btn_redownload_model", "vr.meeting.btn_delete_model", "vr.meeting.btn_pyannote_installed", "vr.meeting.btn_model_installed",
  "vr.meeting.install_success_all", "vr.meeting.install_success_model",
  "vr.meeting.install_success_redownload", "vr.meeting.install_failed", "vr.meeting.install_incomplete",
  "vr.meeting.setup_hint_prefix", "vr.meeting.setup_hint_and", "vr.meeting.setup_hint_suffix",
  "vr.meeting.hint_diarize_title", "vr.meeting.hint_seg_title",
  "vr.meeting.remote_section_title", "vr.meeting.mode_server", "vr.meeting.mode_client",
  "vr.meeting.btn_test_conn", "vr.meeting.pill_remote_client", "vr.meeting.pill_remote_server"
];
check("vi.json and en.json have all meeting mode i18n keys",
  meetingKeys.every(k => typeof viJson[k] === "string" && typeof enJson[k] === "string"));

const hintHtml = VR.renderSetupHintHtml();
check("renderSetupHintHtml includes links with href, title, target, and class for both models",
  typeof hintHtml === "string"
  && hintHtml.includes('href="https://huggingface.co/pyannote/speaker-diarization-3.1"')
  && hintHtml.includes('href="https://huggingface.co/pyannote/segmentation-3.0"')
  && hintHtml.includes('title="vr.meeting.hint_diarize_title"')
  && hintHtml.includes('title="vr.meeting.hint_seg_title"')
  && hintHtml.includes('target="_blank"')
  && hintHtml.includes('rel="noopener noreferrer"')
  && hintHtml.includes('class="vr-link"')
  && consoleCss.includes(".vr-link"));

check("voice-record.js contains 3 tabs including settings tab",
  vrJsSrc.includes("vrTabSettings") && vrJsSrc.includes("vrPaneSettings") && vrJsSrc.includes("vrStPills"));

check("model dropdown moved from meeting pane to settings pane and vrMtPills removed",
  !vrJsSrc.includes('id="vrMtPills"')
  && vrJsSrc.includes('id="vrStPills"')
  && !vrJsSrc.match(/id="vrPaneMeeting"[\s\S]*?id="vrMtModel"[\s\S]*?id="vrPaneSettings"/)
  && vrJsSrc.match(/id="vrPaneSettings"[\s\S]*?id="vrMtModel"/));

check("settings pane arranges model dropdown and CTAs on single row, vrStPills single-line, and compact token row",
  vrJsSrc.includes("vr-mt-model-row")
  && vrJsSrc.includes("vrMtBtnDeleteModel")
  && consoleCss.includes(".vr-mt-model-row")
  && consoleCss.includes("#vrStPills { display: inline-flex; flex-wrap: nowrap")
  && consoleCss.includes(".vr-settings-card .gcard-btn")
  && consoleCss.includes(".vr-btn-ghost")
  && vrJsSrc.includes("btn_pyannote_installed"));

check("voice-record.js contains rediarize buttons and handler",
  vrJsSrc.includes("vrMtBtnRediarizeRec") && vrJsSrc.includes("vrMtBtnRediarizeBanner") && vrJsSrc.includes("rediarizeMeetingRecord"));

check("voice-record.js and console.css wire 4-column CTA bar with TXT primary button group",
  vrJsSrc.includes("vrMtExpGroup") &&
  vrJsSrc.includes("vr-btn-group") &&
  vrJsSrc.includes("vrMtExp_txt") &&
  consoleCss.includes("vr-mt-actions-bar") &&
  consoleCss.includes("grid-template-columns: repeat(4, minmax(0, 1fr))") &&
  consoleCss.includes("vr-btn-group-item.is-primary"));

// 9. Install / Download / Redownload CTA locking & state machine
const baseSt = {
  ok: true,
  installing: false,
  lib_ready: true,
  venv_diarize_ready: false,
  cached_whisper_models: { "large-v3-turbo": false, medium: true, small: false },
};
const sUnready = VR.getInstallCtaState({ model: "large-v3-turbo", installPending: "" }, baseSt);
check("uncached turbo + missing pyannote: installAll & installModel enabled, redownload hidden",
  sUnready.installAll.disabled === false
  && sUnready.installModel.disabled === false
  && sUnready.redownloadModel.visible === false);

const sCachedMed = VR.getInstallCtaState({ model: "medium", installPending: "" }, baseSt);
check("cached medium + missing pyannote: installAll enabled, installModel lockedDone, redownload visible & enabled",
  sCachedMed.installAll.disabled === false
  && sCachedMed.installModel.lockedDone === true
  && sCachedMed.installModel.disabled === true
  && sCachedMed.redownloadModel.visible === true
  && sCachedMed.redownloadModel.disabled === false
  && sCachedMed.deleteModel.visible === true
  && sCachedMed.deleteModel.disabled === false);

check("uncached turbo: deleteModel hidden",
  sUnready.deleteModel.visible === false);

const sClicking = VR.getInstallCtaState({ model: "medium", installPending: "all" }, baseSt);
check("while pending/installing: installAll, installModel, redownload all temporarily locked",
  sClicking.busy === true
  && sClicking.installAll.disabled === true
  && sClicking.installModel.disabled === true
  && sClicking.redownloadModel.disabled === true
  && sClicking.deleteModel.disabled === true);

const sDone = VR.getInstallCtaState(
  { model: "large-v3-turbo", installPending: "" },
  { ok: true, installing: false, lib_ready: true, venv_diarize_ready: true, cached_whisper_models: { "large-v3-turbo": true } }
);
check("when pyannote + model validly installed: both installAll & installModel lockedDone, redownload enabled, installAll button hidden",
  sDone.installAll.lockedDone === true
  && sDone.installAll.disabled === true
  && sDone.installAll.visible === false
  && sUnready.installAll.visible === true
  && sDone.installModel.lockedDone === true
  && sDone.installModel.disabled === true
  && sDone.redownloadModel.visible === true
  && sDone.redownloadModel.disabled === false);

// 10. Remote Speaker Diarization UI elements
check("voice-record.js contains remote diarization mode buttons and key fields",
  vrJsSrc.includes("vrModeBtnServer")
  && vrJsSrc.includes("vrModeBtnClient")
  && vrJsSrc.includes("vrServerApiKey")
  && vrJsSrc.includes("vrClientApiKey")
  && vrJsSrc.includes("vrBtnPingRemote")
  && vrJsSrc.includes("vrBtnGenServerKey"));

// 11. Background Task Queue Monitoring
check("fmtEstimatedTime handles empty, seconds, minutes, hours",
  VR.fmtEstimatedTime(0) === "--"
  && VR.fmtEstimatedTime(45).startsWith("~45")
  && VR.fmtEstimatedTime(180).startsWith("~3")
  && VR.fmtEstimatedTime(3660).startsWith("~1"));

check("buildOriginBadge renders local vs client origin",
  VR.buildOriginBadge("local").includes("is-local")
  && VR.buildOriginBadge("192.168.1.50").includes("is-client"));

check("buildStatusBadge renders queued, running, done, and failed",
  VR.buildStatusBadge({ status: "queued", queue_position: 3 }).includes("is-queued")
  && VR.buildStatusBadge({ status: "transcribing", progress: 65 }).includes("is-running")
  && VR.buildStatusBadge({ status: "done" }).includes("is-done")
  && VR.buildStatusBadge({ status: "failed" }).includes("is-failed"));

check("voice-record.js contains queue section, wrap, and refresh button",
  vrJsSrc.includes("vrQueueSection")
  && vrJsSrc.includes("vrQueueWrap")
  && vrJsSrc.includes("vrBtnRefreshQueue")
  && vrJsSrc.includes("fetchMeetingQueue")
  && vrJsSrc.includes("renderMeetingQueue"));

const qKeys = [
  "vr.queue.title",
  "vr.queue.desc",
  "vr.queue.empty",
  "vr.queue.col_file",
  "vr.queue.col_origin",
  "vr.queue.col_duration",
  "vr.queue.col_estimated",
  "vr.queue.col_status",
  "vr.queue.origin_local",
  "vr.queue.origin_client",
  "vr.queue.status_queued",
  "vr.queue.status_running",
];
check("vi.json and en.json have all queue monitor i18n keys",
  qKeys.every(function (k) { return viJson[k] && enJson[k]; }));

// 12. Meeting CTA locking and busy state transitions
check("isMeetingBusy returns false for idle meeting without active job",
  VR.isMeetingBusy({ recState: "idle" }) === false);

check("isMeetingBusy returns true for recording, paused, uploading, normalizing, or rediarizing",
  VR.isMeetingBusy({ recState: "recording" }) === true
  && VR.isMeetingBusy({ recState: "paused" }) === true
  && VR.isMeetingBusy({ recState: "idle", uploading: true }) === true
  && VR.isMeetingBusy({ recState: "idle", normalizing: true }) === true
  && VR.isMeetingBusy({ recState: "idle", rediarizing: true }) === true);

check("isMeetingBusy returns true for active background job stages",
  VR.isMeetingBusy({ recState: "idle", activeJob: { status: "queued" } }) === true
  && VR.isMeetingBusy({ recState: "idle", activeJob: { status: "converting" } }) === true
  && VR.isMeetingBusy({ recState: "idle", activeJob: { status: "transcribing" } }) === true
  && VR.isMeetingBusy({ recState: "idle", activeJob: { status: "diarizing" } }) === true
  && VR.isMeetingBusy({ recState: "idle", activeJob: { status: "merging" } }) === true);

check("isMeetingBusy returns false once job is done or failed",
  VR.isMeetingBusy({ recState: "idle", activeJob: { status: "done" } }) === false
  && VR.isMeetingBusy({ recState: "idle", activeJob: { status: "failed" } }) === false);

const busyMt = { recState: "idle", rediarizing: true };
const idleMt = { recState: "idle", rediarizing: false };
const mtRec = { mode: "meeting", has_audio: true };

check("dependent meeting CTAs are blocked while busy and allowed when idle",
  VR.canNormalizeMeeting(busyMt) === false
  && VR.canNormalizeMeeting(idleMt) === true
  && VR.canRediarizeMeeting(busyMt, mtRec) === false
  && VR.canRediarizeMeeting(idleMt, mtRec) === true
  && VR.canExportMeeting(busyMt) === false
  && VR.canExportMeeting(idleMt) === true
  && VR.canCreateNewRecord(busyMt) === false
  && VR.canCreateNewRecord(idleMt) === true
  && VR.canMeetingStart(busyMt) === false
  && VR.canMeetingStart(idleMt) === true
  && VR.canNewRecord({ state: "idle" }, busyMt) === false
  && VR.canNewRecord({ state: "idle" }, idleMt) === true);

check("voice-record.js wires disabled states for normalize, rediarize, new record, export group, and banner",
  vrJsSrc.includes("normBtn.disabled = mtBusy")
  && vrJsSrc.includes("rediarizeBtn.disabled = mtBusy")
  && vrJsSrc.includes("newRecBtn.disabled = mtBusy")
  && vrJsSrc.includes('expGroup.classList.toggle("is-disabled", mtBusy)')
  && vrJsSrc.includes("btnBanner.disabled = mtBusy")
  && vrJsSrc.includes("applySpkBtn.disabled = mtBusy"));

check("console.css contains disabled styling for vr-act-btn, vr-btn-group, vr-btn-pin, and banner",
  consoleCss.includes(".vr-mt-actions-bar .vr-act-btn:disabled")
  && consoleCss.includes(".vr-btn-group.is-disabled")
  && consoleCss.includes(".vr-btn-pin:disabled")
  && consoleCss.includes(".vr-btn-rediarize-banner:disabled"));

// 12. Background Queue Table Time Columns, Estimation, and Worker Log link
check("fmtClockTime returns -- for empty or invalid values",
  VR.fmtClockTime(null) === "--"
  && VR.fmtClockTime(undefined) === "--"
  && VR.fmtClockTime(0) === "--"
  && VR.fmtClockTime("") === "--"
  && VR.fmtClockTime("invalid-date") === "--");

check("fmtClockTime formats valid ISO strings and timestamps into clock format",
  /^\d{2}:\d{2}:\d{2}/.test(VR.fmtClockTime("2026-11-13T10:15:30+07:00"))
  && /^\d{2}:\d{2}:\d{2}/.test(VR.fmtClockTime(1731512345)));

const failedWithLogBadge = VR.buildStatusBadge({ id: "job123", status: "failed", has_log: true });
check("buildStatusBadge includes Log link for failed jobs with log",
  failedWithLogBadge.includes("vr-queue-log-link")
  && failedWithLogBadge.includes("/voice-records/meeting/jobs/job123/log")
  && failedWithLogBadge.includes("Log"));

const failedNoLogBadge = VR.buildStatusBadge({ id: "job456", status: "failed", has_log: false });
check("buildStatusBadge omits Log link when has_log is false",
  !failedNoLogBadge.includes("vr-queue-log-link"));

check("voice-record.js contains started, finished, and actual queue table headers in order",
  vrJsSrc.includes('<th>\' + esc(tw("vr.queue.col_started")) + \'</th>')
  && vrJsSrc.includes('<th>\' + esc(tw("vr.queue.col_finished")) + \'</th>')
  && vrJsSrc.includes('<th>\' + esc(tw("vr.queue.col_actual")) + \'</th>')
  && vrJsSrc.includes('<th>\' + esc(tw("vr.queue.col_estimated")) + \'</th>'));

check("console.css contains styling for vr-queue-log-link and column classes",
  consoleCss.includes(".vr-queue-log-link")
  && consoleCss.includes(".vr-queue-col-started")
  && consoleCss.includes(".vr-queue-col-finished")
  && consoleCss.includes(".vr-queue-col-actual"));


if (fails.length) {
  console.log("\nFAIL:", fails.length, fails);
  process.exit(1);
}
console.log("\nOK - voice_record");

