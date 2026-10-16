# T3 phần L: học trong đội (0.156.0)

Thiết kế: `docs/superpowers/specs/2026-10-15-team-adaptive-design.md` mục 3.2-3.4, 4.4, 6.4, 8, 9, 10, 12. Hợp đồng ba phần:
`docs/dev/team-chat-t3-contract.md` (L sở hữu mục 3 và phần bài học của mục 4). Phần này gồm năm việc: lượt xem xét reaction thật,
kho bài học có phạm vi, góp ý của đồng đội, tự rà soát có tự áp và tự quay lại, hạn mức lượt tự phát mỗi agent.

## 1. Module và bảng

| Module | Việc | Bảng (đều trong `resonance.sqlite3`, module tự tạo, `CREATE TABLE IF NOT EXISTS`) |
|---|---|---|
| `server/agent_lessons.py` | kho bài học có phạm vi, góp ý của đồng đội, điểm nối `TeamLearning` cho bộ chạy nhóm | `agent_lessons`, `agent_lesson_events`, `agent_peer_feedback` |
| `server/agent_review.py` | `ReviewHost`: lượt xem xét reaction (4.4), tự rà soát (8.3), hàm quay lại (6.4), mục "lessons" của khung nhịp, route của chủ | `agent_review_state`, `agent_review_changes`, `agent_review_consumed`, `agent_review_proposals` |
| `server/agent_budget.py` | `try_spend` theo trần `agent_daily_calls` (mục 9) | `agent_budget`, `agent_budget_waits` |
| `dashboard/agent-lessons-ui.js` | mục "Bài học và hạn mức" trong bảng Nhịp (sổ `window.JavisPulseParts`) | |

Sửa nhỏ ở chỗ có sẵn: `feedback_projector.FeedbackProjector.review_source/set_review_status` (đọc nguồn `rx:*`, ghi trạng thái theo
kết quả host thật), `team_runner` (khối bài học phạm vi nhóm, khối góp ý, lệnh `peer_feedback`, gắn tin vào bài học, dòng
`agent_daily_calls` của bảng giới hạn), `team_api.TeamApiDeps.learning`, `main.py` (đường ghi `JAVIS_LESSON`, lời nhắc chat riêng,
nối host học, nhịp học trước nhịp của P, route).

### `agent_lessons`

`id, agent_key, scope (team:<id>|dm:<agent_key>|*), kind (speak|method|presentation|collab), text, text_norm, source (owner|teammate|
leader|self|review), source_refs_json, status (active|revoked|superseded), promoted_from, created_at, revoked_at, revoked_by,
revoke_reason`. Chỉ mục duy nhất từng phần `(agent_key, scope, text_norm) WHERE status = 'active'`: thêm trùng chữ chỉ nối nguồn. Trần
40 bài học đang dùng mỗi phạm vi (bài học tự học cũ nhất, không phải của chủ, chuyển `superseded`). Nhật ký `agent_lesson_events`
(`add`, `dup`, `revoke`, `restore`, `promote`, `supersede`, `link`): không xoá dòng nào.

### `agent_peer_feedback`

`id, team_id, receiver_key, author_key, author_role (teammate|leader), message_id, task_id, revision, turn_ref, note, status
(active|evidence), evidence_reason, nonce UNIQUE, delivered_turn, created_at, delivered_at`.

### `agent_review_changes`

Mỗi thay đổi tự rà soát đề xuất (áp hay không): `kind`, `signature` (`kind|ngữ cảnh|đích`: hướng lên/xuống cho độ lệch, giá trị cho
độ hăng hái, chữ đã chuẩn hoá cho thêm bài học, mã bài học cho bỏ/nâng), `before_json`, `after_json`, `evidence_json`, `why`, `status
(applying|applied|kept|reverted|rejected)`, `reject_reason`, `policy_change_id` (dòng `pulse_policy_changes` của P), `reverted_at`,
`revert_reason`.

## 2. Luồng

### 2.1 Lượt xem xét reaction (4.4)

1. Bộ chiếu T2 gom reaction hợp lệ (Cộng hưởng bật, sau `resumed_at`, tin không gắn goal) thành MỘT lý do `reaction_review` mỗi (agent,
   ngữ cảnh). Host nhịp của P (`PulseHost.tick`) spawn bộ xử lý đã đăng ký cho lý do `pending` (bỏ qua agent đang bận, tạm dừng, tắt).
2. `ReviewHost.serve_reaction_review(reason)`: thử khoá bận KHÔNG chờ (bận: `busy`, lý do giữ `pending`, attempt không đổi), hỏi
   `agent_budget.try_spend(key, "reaction_review", reason_ref="wr:<id>", units=2 ở chat riêng)` (chat riêng cần chỗ cho cả lượt
   xem xét và lượt phán xử tin mới; hết: mọi reaction của nguồn sang `waiting_quota` "Chờ hạn mức", sự kiện `quota`, không sổ lượt;
   ghi trạng thái, sự kiện, gói WS tối đa một lần mỗi 10 phút mỗi lý do), rồi `WakeReasons.claim` (CAS, chốt danh sách nguồn, giữ chỗ `agent_calls` purpose
   `reaction_review`, `obligation = 0`; trần chung đầy: "Chờ hạn mức" lý do `server_ceiling`).
3. Reaction của nguồn sang `reviewing`. Lời nhắc: luật xem xét (`REVIEW_RULES`) nối sau persona; thân là từng nguồn `[rx:..]` với đoạn
   trích tin, emoji, lý do, trục nói/im, góp ý, cờ "chủ đã đổi phản hồi này sau đó"; cộng khối bài học đúng phạm vi ngữ cảnh.
4. Engine: `deps.engine_for(key, ctx, "reaction_review")`, ở `main.py` là `_pulse_engine_for` (cùng hợp đồng chỉ chữ của lượt nhóm và
   quét). Mốc engine ghi vào sổ; hết giờ 10 phút.
5. `parse_review`: một khối JSON, chỉ các khoá `outcome, message, lesson, consumed, reason`; `outcome` thuộc bốn giá trị; `consumed`
   là danh sách KHÁC RỖNG và ⊆ nguồn; `message` khác rỗng với `ask_back`/`revise`; `lesson {text, kind presentation|method}` bắt buộc
   với `lesson`. Sai: `W.settle(error)` (lý do về `pending` attempt + 1, quá 3 lần `failed`), reaction về "Chờ xem xét" kèm lý do,
   sự kiện `reaction_review_error`.
6. Đúng: `W.settle(ok, consumed)` TRƯỚC khi gây tác động (nguồn chưa tiêu thụ sang lý do `pending` cùng loại, reaction của chúng về
   "Chờ xem xét"); rồi tác động:
   - `keep`: không gửi; "Không cần đổi" kèm lý do của model.
   - `lesson`: `agent_lessons.add(scope = ngữ cảnh, source = review, nguồn = consumed + wr:<id> + call:<id>)`; "Đã điều chỉnh".
   - `ask_back`/`revise`: `judge.proactive(agent_key, context_ref, draft, source_event = "wr:<id>", origin = "reaction_review",
     payload = {judge_daily (nhóm), draft, review{..., session_id}})`. `speak`: `deps.send` gửi TIN MỚI trả lời tin bị phản hồi
     (không sửa đè); ở chat riêng tin vào ĐÚNG phiên có tin bị phản hồi, kèm `reply_to` và dấu `review` (phiên đó không phải chat
     riêng của trợ lý thì về phiên báo cáo). Gửi được thì "Đã điều chỉnh", không thì "Không cần đổi" ("Chưa gửi được tin mới").
     Bộ phán xử dừng vì hết hạn mức hay chạm trần (`waiting_quota`, `judge_cap`, `server_ceiling`): "Chờ hạn mức", nguồn về lý do
     chờ (`WakeReasons.requeue`), lùi 10 phút; không phải "phán xử chọn im". `silent`: "Không cần đổi" ("Phán xử chọn im"). `wait`:
     "Không cần đổi" ("Phán xử chọn chờ"); khi J gỡ chờ và xét lại thành `speak` (chat riêng và nhóm), bộ gửi `on_judge_send` (chỉ
     nhận `origin = reaction_review`) gửi tin và đổi trạng thái sang "Đã điều chỉnh".
7. Trạng thái ghi lên MỌI reaction trong `consumed` (kho chat riêng và kho nhóm, chỉ khi reaction còn ở đúng lần đổi đó), gói WS
   `chat_reaction`/`team_message`. Quyết định ghi `agent_pulse_events` kind `reaction_review` (reason = outcome, payload: reason_id,
   call_id, consumed, moved_to, status, lesson_id/decision_id/message_id).

### 2.2 Bài học có phạm vi (8.1)

- Lượt nhóm: `_team_engine_for` chỉ BÓC `JAVIS_LESSON`/`JAVIS_RULE` (hạ thành bài học); `commit` (bộ chạy chỉ gọi khi đầu ra còn hợp lệ,
  R3, một lần mỗi lượt) ghi `team:<team_id>`, nguồn `self`, nguồn tham chiếu `turn:<id>`, `task:<id>`, `step:<id>`; sau khi đăng tin bộ
  chạy gắn thêm `msg:<id>`. Đội giả của lượt quét/xem xét (không có `session_id`) không bao giờ ghi.
- Chat riêng: `_ket_luot_agent(..., conv_sid)` ghi `JAVIS_RULE` vào "Luật chủ dặn" như cũ; `JAVIS_LESSON` (và `JAVIS_RULE` không có câu
  của chủ) vào `dm:<agent_key>` (mã: ghim của phiên, dòng sống của slug, không có thì cấp mã không bật Auto). Không còn vào MEMORY.md.
- Lời nhắc: lượt nhóm nạp `team:<id>` + `*` (`TeamRunner._learning_lines`), chat riêng nạp `dm:<agent_key>` + `*`
  (`_dm_lessons_prompt`). `for_prompt` lọc đúng phạm vi đã cho, không bao giờ trả phạm vi khác.
- Bài học cũ của MEMORY.md giữ nguyên (vẫn trong lời nhắc như trước), giao diện ghi "Bài học cũ chưa có phạm vi" (đọc bằng
  `legacy_lessons`, không di trú).
- Nâng lên `*`: chủ (route `promote`) hay tự rà soát với bằng chứng từ ≥ 2 ngữ cảnh khác nhau; bài học gốc `superseded`.
- Thu hồi: `revoked`, nhật ký giữ; khôi phục chỉ dùng khi quay lại một thay đổi tự rà soát.

### 2.3 Góp ý của đồng đội (8.2)

Lệnh `<!-- JAVIS_TEAM: {"op": "peer_feedback", "message_id": .., "note": ..} -->` trong mọi vai, tối đa 3 mỗi lượt, xử lý trong
`accept_lock` cùng chỗ với lệnh `react`. Host gán `author_role` (trưởng nhóm của nhóm hay đồng đội), người nhận là tác giả tin
(phải là tin của trợ lý khác trong đúng nhóm), gắn `task_id` và `revision` của tin, `turn_ref`, nonce `<turn>#pf<i>`. Đầu ra không còn
hiệu lực (thành viên rời, huỷ, sửa yêu cầu, đổi trưởng nhóm, mất quyền giữ) hay người nhận đã rời: `status = evidence`, không bao giờ
hiện. Lúc dựng lượt kế tiếp của người nhận trong đúng nhóm: góp ý trỏ việc đã huỷ hay bản đã đổi bị hạ thành bằng chứng; còn lại hiện
dưới "Góp ý của đồng đội (không phải yêu cầu của chủ, không phải sự thật đã kiểm)" kèm câu "nhiều trợ lý cùng ý cũng không phải
bằng chứng độc lập"; lượt được chấp nhận thì đánh dấu đã giao (một lần). Góp ý không tạo bài học, luật hay tín hiệu hữu ích.

### 2.4 Tự rà soát và quay lại (8.3, 6.4)

- `ReviewHost.tick(agents)` chạy trong `main._learn_tick` NGAY TRƯỚC `PulseHost.tick` mỗi 30 giây: cập nhật số nhãn/lỗi cho cửa sổ
  quan sát đang mở của mình (`agent_pulse.record_policy_labels`), đồng bộ `kept`/`reverted` từ P, kiểm mỗi agent tối đa một lần mỗi 10
  phút và spawn `maybe_self_review` khi đến hạn.
- Đến hạn (`due`): Cộng hưởng bật; ≥ 24 giờ từ lần rà soát thành công trước; ≥ 1 giờ từ lần thử lỗi; bằng chứng mới sau
  `max(resumed_at, lần trước)`: ≥ 6 nhãn nói/im (J, bỏ lời gọi trực tiếp) hay ≥ 5 tín hiệu hữu ích (P, bỏ `context_init`), trừ nguồn
  đã được lượt xem xét reaction tiêu thụ, nguồn CÒN nằm trong lý do `reaction_review` chưa xong (chờ, đang chạy), nhãn sinh từ trục
  nói/im của reaction (`label_source = reaction`), và nguồn lần rà soát trước đã dùng.
- Lượt: khoá bận, `try_spend(key, "review")`, giữ chỗ `agent_calls` purpose `review` (`selfreview:<key>:<ms>`, obligation 0), lời nhắc
  `SELF_RULES` với mã bằng chứng `[decision:<id>]`, `[<source_ref>]` và bài học `[L<id>]`.
- Mỗi thay đổi (tối đa 3, phần thừa `over_limit`): loại ngoài danh sách đóng thành đề xuất; bằng chứng phải là mã đã cho xem; `offset_set`
  (ngữ cảnh của agent, kẹp ±0,25, không đổi thì bỏ), `eagerness_set` (low|medium|high), `lesson_add` (team/dm, cần ít nhất một
  bằng chứng của CHÍNH ngữ cảnh đích, không thì `evidence_context`), `lesson_remove`
  (không phải bài học của chủ, không phải `*`), `lesson_promote` (bằng chứng ≥ 2 ngữ cảnh). Không đè thiết lập chủ đang giữ (`owner_held`).
  Không lặp chữ ký đã quay lại trong 14 ngày.
- Áp ngay qua J (`set_offset`/`set_eagerness` với nguồn `self_review:<mã>`) hay kho bài học; ghi trước/sau; nối cửa sổ của P: dùng dòng
  `pulse_policy_changes` J đã ghi qua móc `policy_change` (nguồn trùng), không có thì tự ghi; đặt `base_error` (tỷ lệ nhãn ngược kết
  luận trong 30 ngày của ngữ cảnh) để luật "+0,15 khi ≥ 6 nhãn" có mốc.
- Quay lại: P đóng cửa sổ (≥ 6 nhãn hay ≥ 5 tín hiệu mới hay 14 ngày) và gọi hàm quay lại đã đăng ký cho loại đó
  (`agent_pulse.register_policy_reverter`). Hàm của L chỉ xử lý nguồn `self_review:*`: độ lệch bỏ ĐÚNG phần dịch của thay đổi (đã
  nhạt dần như độ lệch), nên phần chủ dạy bằng nhãn trong cửa sổ được giữ; độ hăng hái về trước (chủ đã đặt lại thì giữ của chủ, ghi
  `owner_held`); thu hồi bài học đã thêm, khôi phục bài học đã bỏ, quay lại phép nâng. Chủ bấm Quay lại trên thẻ dùng route của P
  `POST /agents/{slug}/pulse/revert {change_id}`; lý do ghi `owner`.
- Tiến trình chết giữa `applying` và `applied`: quá 10 phút thì thay đổi thành `interrupted` và hiện trong mục Bài học kèm trước/sau
  (không có cửa sổ 6.4, chủ kiểm và sửa tay).
- Đề xuất ngoài phạm vi (quyền, luật chủ, mã, skill, workflow, cấu trúc) vào `agent_review_proposals`, hiện ở mục "Đề xuất cần bạn quyết".

### 2.5 Hạn mức (mục 9)

`try_spend(agent_key, purpose, at=None, *, store=None, reason_ref=None) -> (ok, reason)` chỉ KIỂM; lượt được tính khi người gọi giữ chỗ
sổ lượt ngay sau đó. Đếm: dòng `agent_calls` của agent từ 0 giờ (giờ máy), trừ `refunded`, purpose `proactive|scan|review|reaction_review`
cộng `judge_batch` ở ngữ cảnh `dm:` (phán xử tin chủ động ở chat riêng). Hai loại đầu (`reaction_review`, `related_review` nhận qua lý do
`related_review`) dùng tới trần; loại khác chỉ dùng tới `trần - max(0, 2 - số lượt hai loại đầu hôm nay)`. Hết: lý do giữ `pending`,
`agent_budget_waits` ghi lý do đã chờ; ngày sau mỗi lý do đó chỉ được một lượt (`backlog_once`). Trần đặt bằng `set_cap` (chỉ `by =
"owner"`; số 0..200 hay "unlimited"/null; chuỗi rỗng là lỗi 400). `units` (mặc định 1) là số lượt cần còn trống. Host nhịp phục vụ lý do
lớp 3 theo `sort_by_priority`. Dòng `agent_daily_calls` vào bảng giới hạn T1 (nhóm owner, mặc định 6, có Không giới hạn).

## 3. Route (chỉ của chủ, sau middleware auth + CSRF; thành công chỉ sau khi kho đã ghi)

- `POST /agents/{slug}/lessons/{lesson_id}/revoke`, `POST /agents/{slug}/lessons/{lesson_id}/promote` -> `{ok, result, view}` (`view`
  là mục "lessons" mới); 409 khi không còn gì để đổi (đã thu hồi, bài học của trợ lý khác).
- `POST /agents/{slug}/budget {daily_calls: số | "unlimited"}`; 400 khi giá trị hỏng; 409 khi trợ lý chưa có mã.
- Mục "lessons" trong `GET /agents/{slug}/pulse` (`parts.lessons`): `scopes`, `legacy`, `changes` (kèm `can_revert`, `policy_change_id`),
  `proposals`, `budget`, `reviews`, `slug`.

## 4. Lỗi và cách hồi phục

| Tình huống | Kết quả |
|---|---|
| Agent bận (lượt nhóm, A2, quét) | lý do giữ `pending`, attempt không tăng; nhịp sau thử lại |
| Phán xử tin mới dừng vì hết hạn mức | "Chờ hạn mức"; nguồn về lý do chờ (không tiêu thụ), lùi 10 phút |
| Engine không dựng được nhiều lần | mỗi lần hoàn lượt; lý do lùi 5 phút trước lần thử kế |
| Hết hạn mức ngày / trần chung | "Chờ hạn mức"; không sổ lượt, không engine; qua ngày một lượt mỗi lý do |
| Engine không dựng được (trước mốc) | lượt `refunded`, lý do `pending` (không tính lần thử) |
| Engine lỗi sau mốc, hết giờ | lượt `error`, lý do `pending` attempt + 1 (quá 3: `failed`, chủ bấm thử lại) |
| Đầu ra sai khuôn | như trên, sự kiện `reaction_review_error` kèm mã lỗi |
| Lỗi khi áp tác động (sau khi đã chốt `ok`) | trạng thái "Không cần đổi" kèm "Không áp dụng được kết quả xem xét"; không thử lại (tránh tin trùng) |
| Tiến trình chết giữa lượt | lý do kẹt `claimed`: `recover_stale_claims` (mỗi nhịp học) chốt `interrupted` sau 11 phút, nguồn về `pending` (attempt giữ, quá 3 thì `failed`), reaction về "Chờ xem xét"; dòng sổ qua mốc engine vẫn tính |
| Kho bài học lỗi khi dựng lời nhắc | lượt chạy tiếp không có khối bài học (ghi log) |
| P chưa có module | `register_with_pulse` trả False; `pending_reviews()` và `serve_reaction_review` vẫn gọi tay được |
| J chưa nối | phán xử trả `silent` (`judge_unavailable`): `ask_back`/`revise` không bao giờ gửi khi chưa được phán xử; tự rà soát bỏ qua `offset_set`/`eagerness_set` (lỗi `apply_error`) |

## 5. Di trú và quay về bản cũ

- Nâng cấp: chỉ tạo bảng mới. Không backfill: bài học cũ trong MEMORY.md giữ nguyên chỗ cũ; reaction cũ không thành lý do.
- Quay về bản cũ: bản cũ bỏ qua bảng mới; `JAVIS_LESSON` lại ghi MEMORY.md như trước. Bài học có phạm vi ghi trong lúc chạy 0.156.0 vẫn ở
  `agent_lessons` (không mất, chỉ không được nạp) cho tới khi nâng lại. Công cụ khôi phục T2 (`tools/team_adaptive_restore_pre.py`) chép
  nguyên `resonance.sqlite3`, nên các bảng mới nằm trong bộ sao lưu.

## 6. Điểm nối với P và J

- **P (đã ghép vào nhánh này):** `agent_pulse_host.register_reason_handler("reaction_review", ...)`, `register_view_part("lessons",
  ...)`, `agent_pulse.register_policy_reverter(kind, ...)` cho năm loại thay đổi; `apply_policy_change`, `record_policy_labels`,
  `policy_changes`, `record_event`, `events`, `signals`; `main._learn_tick()` chạy trước `_pulse_host().tick()`. Hạn mức của P gọi
  `agent_budget.try_spend(key, "scan", at)` qua bộ chuyển `_budget_try_spend` (module đã có nên không còn "cho qua").
- **J (đã ghép qua `origin/claude/team-adaptive-t3` d8d1b2b1, gọi qua `JudgeAdapter`):** `judge_proactive(..., origin, payload)`
  (async), `get_profile` (đọc `eagerness_source`), `set_offset`, `set_eagerness`, `default().store(False).decisions/changes` (nhãn kèm
  `label_at`, chủ giữ độ lệch).
  1. `main._learn_register_sender()` đăng ký `ReviewHost.on_judge_send` với `agent_judge.register_sender` khi bộ phán xử đã nối (thử
     lại mỗi nhịp). Bộ gửi chung `main._judge_send` BỎ QUA payload `origin = "reaction_review"` (không gửi hai lần); test
     `test_t3_integration.py` kiểm cả hai.
  2. Đã khép (92202224): `_agent_judge()` truyền `policy_change=_judge_policy_change_noop`; nhãn của chủ không mở cửa sổ 6.4 ở P,
     L tự ghi dòng cửa sổ cho thay đổi của mình.
  3. Đã khép cùng mục 2: thay đổi của chủ không mở cửa sổ nên P không quay lại chúng.
  4. Đã khép: `agent-pulse.js` gọi `render(view, null, {slug})`; mục "judge" và "lessons" theo cùng chữ ký.
- **Bộ chạy nhóm:** `TeamRunner(learning=agent_lessons.TeamLearning(...))` qua `TeamApiDeps.learning`.

## 7. Quyết định khi làm

- `consumed` rỗng là sai khuôn: một lượt không tiêu thụ gì sẽ đẩy mọi nguồn sang lý do mới và lặp vô hạn trong hạn mức.
- Chốt `ok` trước khi gây tác động: thà mất một tác động (hiện "Không áp dụng được") còn hơn gửi trùng tin khi chạy lại.
- `message` của `keep`/`lesson` và `lesson` của outcome khác bị bỏ qua (không lỗi); khoá lạ thì lỗi.
- Hạn mức đếm thẳng từ `agent_calls` (không sổ thứ hai): lượt hoàn tự trả lại, không cần ghi "đã tiêu".
- `wait_unblocked` không dùng 2 lượt giữ (thiết kế chỉ giữ cho "hai loại xem xét đầu").
- Hạn mức lưu ở bảng riêng `agent_budget` (không có kho cài đặt theo agent; `max_tier` của P nằm trong `agent_pulse`, không mở rộng bảng
  của P); route `POST /agents/{slug}/budget` cùng khuôn với `POST /agents/{slug}/pulse/max_tier`.
- Tự rà soát đếm bằng chứng sau `max(resumed_at, lần rà soát trước)` và đánh dấu bằng chứng đã dùng; tín hiệu đã được lượt xem xét
  reaction tiêu thụ không đếm (không hai vòng học cho một phản hồi).
- Bước workflow (`_learn` của workflow) vẫn ghi MEMORY.md như trước: thiết kế chỉ đổi chat riêng và lượt nhóm.
- Góp ý của đồng đội giao đúng một lần, cho lượt được CHẤP NHẬN đầu tiên của người nhận trong nhóm đó.
- `learn.py` (rewire, curator) và `resonance_learning.py` (A3) không đọc bảng reaction, `pulse_signals` hay `agent_lessons` (test quét mã).
- Sửa sau review T3 (nhánh `claude/team-adaptive-t3-fix`): lượt phán xử tin mới ở chat riêng tiêu hạn mức ở mức `reaction_review`
  và cần 2 lượt trống trước khi nhận lý do; hết hạn mức ở bước phán xử không làm mất phản hồi (`requeue`); tự rà soát không học lại
  phản hồi đang chờ lượt xem xét reaction; quay lại `offset_set` theo phần dịch; `lesson_add` cần bằng chứng của ngữ cảnh đích.
  Lần chờ của tin mới trong nhóm được xét lại một lần như ở chat riêng (vòng 2 P2-1).
