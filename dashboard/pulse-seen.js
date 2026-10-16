/* Sự kiện "đã xem" của tin trợ lý (T3, 0.156.0; thiết kế 2026-10-15 mục 6.2).

   Tin CỦA TRỢ LÝ nằm trong khung nhìn ≥ 1,5 giây trong lúc tab đang hiển thị thì báo POST /seen {context_ref,
   message_ids, session_id?} một lần (gom lô). Chỉ hai loại phần tử, không theo dõi gì khác:
   - chat nhóm: `.tm-msg-agent[data-mid]` trong khung `[data-seen-team]` (teams.js), ngữ cảnh `team:<id>`;
   - chat riêng: bong bóng trả lời `.msg-javis[data-mid][data-sid]` (chat-reactions.js), ngữ cảnh `session:<mã phiên>`.
     Máy chủ chỉ ghi tin của trợ lý trong phiên `agent:<slug>`; phiên không phải của trợ lý (trang Trò chuyện chính) trả
     `agent: false` và trang thôi gửi cho phiên đó.

   `createTracker` thuần (đồng hồ, hẹn giờ, hàm gửi đều tiêm vào) để test chạy dưới node với đồng hồ giả; `start` nối nó
   với IntersectionObserver + visibilitychange + MutationObserver trong trình duyệt. */
(function () {
  "use strict";

  var DWELL_MS = 1500;
  var FLUSH_MS = 2000;
  var MAX_BATCH = 50;

  function createTracker(o) {
    var opt = o || {};
    var setT = opt.setTimeout || setTimeout, clearT = opt.clearTimeout || clearTimeout;
    var post = opt.post;   // (ctx, ids, sessionId) -> Promise<{agent}>
    var visible = opt.visible !== false;
    var inView = {};       // key -> {ctx, mid, sid, timer}
    var sent = {};         // key -> true (đã báo, không báo lại)
    var muted = {};        // ctx -> true (máy chủ nói không phải ngữ cảnh của trợ lý)
    var queue = {};        // ctx -> {sid, ids: []}
    var flushTimer = null;

    function keyOf(ctx, mid) { return ctx + "#" + mid; }

    function arm(k) {
      var it = inView[k];
      if (!it || it.timer || !visible || sent[k] || muted[it.ctx]) return;
      it.timer = setT(function () {
        it.timer = null;
        if (!visible || !inView[k] || sent[k]) return;
        sent[k] = true;
        var q = queue[it.ctx] || (queue[it.ctx] = { sid: it.sid || "", ids: [] });
        q.ids.push(it.mid);
        if (!flushTimer) flushTimer = setT(flush, FLUSH_MS);
      }, DWELL_MS);
    }

    function disarm(k) {
      var it = inView[k];
      if (it && it.timer) { clearT(it.timer); it.timer = null; }
    }

    function enter(ctx, mid, sid) {
      if (!ctx || mid == null || muted[ctx]) return;
      var k = keyOf(ctx, Number(mid));
      if (sent[k]) return;
      if (!inView[k]) inView[k] = { ctx: ctx, mid: Number(mid), sid: sid || "", timer: null };
      arm(k);
    }

    function leave(ctx, mid) {
      var k = keyOf(ctx, Number(mid));
      disarm(k);
      delete inView[k];
    }

    function setVisible(v) {
      visible = !!v;
      Object.keys(inView).forEach(function (k) { if (visible) arm(k); else disarm(k); });
    }

    function flush() {
      flushTimer = null;
      var jobs = [];
      Object.keys(queue).forEach(function (ctx) {
        var q = queue[ctx];
        delete queue[ctx];
        for (var i = 0; i < q.ids.length; i += MAX_BATCH) jobs.push({ ctx: ctx, sid: q.sid, ids: q.ids.slice(i, i + MAX_BATCH) });
      });
      return Promise.all(jobs.map(function (j) {
        if (!post) return null;
        return Promise.resolve(post(j.ctx, j.ids, j.sid)).then(function (res) {
          if (res && res.agent === false) muted[j.ctx] = true;
        }, function () {
          // Lỗi mạng: cho phép báo lại ở lần thấy sau (không mất sự kiện, không gửi dồn).
          j.ids.forEach(function (mid) { delete sent[keyOf(j.ctx, mid)]; });
        });
      }));
    }

    return { enter: enter, leave: leave, setVisible: setVisible, flush: flush,
      state: function () { return { inView: Object.keys(inView), sent: Object.keys(sent), muted: Object.keys(muted),
        queued: JSON.parse(JSON.stringify(queue)) }; } };
  }

  /* Ngữ cảnh của một phần tử (null: không theo dõi). */
  function contextOf(el) {
    if (!el || !el.getAttribute) return null;
    var mid = el.getAttribute("data-mid");
    if (!mid) return null;
    if (el.classList && el.classList.contains("tm-msg-agent")) {
      var box = el.closest ? el.closest("[data-seen-team]") : null;
      var team = box && box.getAttribute("data-seen-team");
      return team ? { ctx: "team:" + team, mid: Number(mid), sid: "" } : null;
    }
    if (el.classList && el.classList.contains("msg-javis")) {
      var sid = el.getAttribute("data-sid");
      return sid ? { ctx: "session:" + sid, mid: Number(mid), sid: sid } : null;
    }
    return null;
  }

  var SELECTOR = ".tm-msg-agent[data-mid], .msg-javis[data-mid][data-sid]";

  function start() {
    if (typeof window === "undefined" || typeof IntersectionObserver === "undefined" || window.__javisSeen) return null;
    var tr = createTracker({
      visible: document.visibilityState === "visible",
      post: function (ctx, ids, sid) {
        var body = { context_ref: ctx, message_ids: ids };
        if (sid) body.session_id = sid;
        return fetch("/seen", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body) }).then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); });
      }
    });
    var watched = typeof WeakSet !== "undefined" ? new WeakSet() : null;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        var c = contextOf(e.target);
        if (!c) return;
        if (e.isIntersecting && e.intersectionRatio >= 0.5) tr.enter(c.ctx, c.mid, c.sid);
        else tr.leave(c.ctx, c.mid);
      });
    }, { threshold: [0, 0.5, 1] });
    function scan(root) {
      var list = root && root.querySelectorAll ? root.querySelectorAll(SELECTOR) : [];
      Array.prototype.forEach.call(list, function (el) {
        if (watched && watched.has(el)) return;
        if (watched) watched.add(el);
        io.observe(el);
      });
    }
    document.addEventListener("visibilitychange", function () { tr.setVisible(document.visibilityState === "visible"); });
    var mo = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var m = muts[i];
        if (m.type === "attributes") { scan(m.target.parentNode || m.target); continue; }
        Array.prototype.forEach.call(m.addedNodes || [], function (n) {
          if (n.nodeType !== 1) return;
          if (n.matches && n.matches(SELECTOR)) { if (!watched || !watched.has(n)) { if (watched) watched.add(n); io.observe(n); } }
          scan(n);
        });
      }
    });
    mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-mid"] });
    scan(document.body);
    window.__javisSeen = tr;
    return tr;
  }

  var out = { createTracker: createTracker, contextOf: contextOf, start: start, DWELL_MS: DWELL_MS, FLUSH_MS: FLUSH_MS,
    SELECTOR: SELECTOR };
  if (typeof window !== "undefined") {
    window.JavisSeen = out;
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
  }
  if (typeof module !== "undefined" && module.exports) module.exports = out;
})();
