/* chat-upload.js - one POST /upload with progress and stall detection, shared by the main chat and the Team chat.

   Moved out of app.js (guiUpload, 0.64.43) so the Team chat stops waiting forever on a dead upload. Owner, 2026-09-24:
   "thi thoảng gửi ảnh hoặc file lên nó cứ bị quay mãi". A plain fetch shows no progress and, when the connection dies
   half way (Wi-Fi switching, a proxy holding a dead socket), spins until the browser gives up minutes later. Here:
     - progress of the bytes SENT (fetch cannot report it, so XHR), and a separate "server is saving" moment once 100%
       is sent, so the user sees whether the network or the server is slow;
     - a STALL watch instead of a hard ceiling: a big file on a slow line keeps going as long as bytes move; no byte for
       STALL_MS cuts the request ("stall"), and a server silent SERVER_WAIT_MS after the last byte cuts it ("server").
   The caller decides about retries (RETRIES is the main chat's count: stall and net are retried, server and client not).

   send(fd, onProgress, opt) resolves { status, text } or rejects an Error with `kind`: "stall", "server", "net" or
   "client" (the browser threw while building the request; `detail` holds its message). onProgress(loaded, total) on
   each progress event and onProgress(-1, -1) once everything is sent. opt.XHR / opt.clock / opt.stallMs / opt.waitMs /
   opt.url are for tests. */
(function () {
  "use strict";

  var STALL_MS = 30000;        // no byte moved for this long: the connection is dead
  var SERVER_WAIT_MS = 90000;  // everything sent and the server silent for this long: give up
  var RETRIES = 2;             // automatic retries after a stall or a dropped connection (besides the first try)

  function send(fd, onProgress, opt) {
    opt = opt || {};
    var XHR = opt.XHR || XMLHttpRequest;
    // Wrapped in functions on purpose (0.64.51): calling a bare `setInterval` stored on an object makes the browser see
    // that object as `this` and throw "Illegal invocation" before anything is sent.
    var clock = opt.clock || {
      now: function () { return Date.now(); },
      setInterval: function (fn, ms) { return setInterval(fn, ms); },
      clearInterval: function (id) { clearInterval(id); }
    };
    var stallMs = opt.stallMs || STALL_MS, waitMs = opt.waitMs || SERVER_WAIT_MS;
    return new Promise(function (resolve, reject) {
      var xhr = new XHR();
      var mark = clock.now(), sent = false, done = false, why = null;
      var cut = function (kind) { if (done) return; why = kind; try { xhr.abort(); } catch (e) {} };
      var watch = clock.setInterval(function () {
        var idle = clock.now() - mark;
        if (!sent && idle > stallMs) cut("stall");
        else if (sent && idle > waitMs) cut("server");
      }, 2000);
      var finish = function (fn, v) { if (done) return; done = true; clock.clearInterval(watch); fn(v); };
      var fail = function (kind) { var e = new Error(kind); e.kind = kind; return e; };
      xhr.upload.onprogress = function (e) {
        mark = clock.now();
        if (onProgress && e.lengthComputable) onProgress(e.loaded, e.total);
      };
      xhr.upload.onload = function () { sent = true; mark = clock.now(); if (onProgress) onProgress(-1, -1); };
      xhr.onload = function () { finish(resolve, { status: xhr.status, text: xhr.responseText }); };
      xhr.onerror = function () { finish(reject, fail("net")); };
      xhr.onabort = function () { finish(reject, fail(why || "net")); };
      try {
        xhr.open("POST", opt.url || "/upload");
        xhr.send(fd);
      } catch (e) {
        var err = fail("client");
        err.detail = (e && e.message) || String(e);
        finish(reject, err);
      }
    });
  }

  var API = { send: send, STALL_MS: STALL_MS, SERVER_WAIT_MS: SERVER_WAIT_MS, RETRIES: RETRIES };
  if (typeof window !== "undefined") window.JavisUpload = API;
  if (typeof module !== "undefined" && module.exports) module.exports = API;
})();
