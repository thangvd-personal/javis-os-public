// 0.267.0: the Team chat no longer hangs on a phone (dashboard/teams.js).
// A user's iPhone, 21/10/2026: "hay bị treo khung chat, thoát hẳn xong vào lại mới được".
//
//     node tests/js/test_teams_resume.js
"use strict";
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "dashboard", "teams.js"), "utf8");
let fails = 0;
function check(name, cond) {
  console.log((cond ? "ok   " : "FAIL ") + name);
  if (!cond) fails++;
}

check("every request gives up after a while (a hung fetch kept Send disabled forever)",
  /var CALL_MS = 30000;/.test(src) && /opt\.signal = ctl\.signal; timer = setTimeout\(function \(\) \{ ctl\.abort\(\); \}, CALL_MS\);/.test(src)
  && /function \(x\) \{ clearTimeout\(timer\); return x; \}, function \(e\) \{ clearTimeout\(timer\); throw e; \}/.test(src));
// Since the pending bubble (teams.js postEntry): the composer is free at once, and a send that gave up turns the
// bubble "Not sent" with Try again, which posts the SAME body and client id (test_team_pending_send.js).
check("a send that gave up leaves the composer usable and keeps its client id for the retry",
  /\}\)\.catch\(function \(\) \{ failed\(tw\("teams\.err_network"\)\); \}\);/.test(src)
  && /call\("POST", teamUrl\("\/messages"\), entry\.body\)/.test(src) && /if \(oe && oe\.state === "failed"\) \{ postEntry\(oe\);/.test(src));
check("back after 20 s or more: the open team is read again (dead socket on iPhone), a short switch reloads nothing",
  /var AWAY_MS = 20000, hiddenAt = 0;/.test(src)
  && /if \(document\.visibilityState === "hidden"\) \{ hiddenAt = Date\.now\(\); return; \}/.test(src)
  && /if \(away\) \{\s*if \(S\.jumped === null \|\| S\.jumped === undefined\) loadMsgs\(false\);\s*loadQueue\(\); loadTeam\(\);/.test(src));
check("the resume hook is still bound to visibilitychange", /document\.addEventListener\("visibilitychange", onVisible\)/.test(src));
check("no em or en dash", src.indexOf(String.fromCharCode(0x2014)) < 0 && src.indexOf(String.fromCharCode(0x2013)) < 0);
process.exit(fails ? 1 : 0);
