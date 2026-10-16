# Hợp đồng T3: ranh giới module giữa nhịp thích nghi, bộ phán xử và học trong đội (0.156.0)

T3 làm theo `docs/superpowers/specs/2026-10-15-team-adaptive-design.md` mục 5 tới 10, chia ba phần làm song song rồi ghép. File này chốt
các hàm công khai giữa ba phần, để mỗi phần gọi phần kia qua đúng một chỗ. Đổi chữ ký thì sửa file này cùng commit. Mọi lượt model đi
qua sổ lượt chung của T2 (`agent_calls`, giữ chỗ trước engine, mục 3.1 và 9.1) và khoá bận theo agent (3.4).

## 0. Ai làm gì

| Phần | Module mới | Bảng | Ghi chú |
|---|---|---|---|
| P (nhịp) | `server/agent_pulse.py` (chính sách thuần `pulse.v1`), `server/agent_pulse_host.py` (nhịp 30 giây, lần thức, quét) | `agent_pulse` (đã có từ T2, mở rộng), `agent_pulse_events`, `pulse_signals`, `pulse_contrib`, `message_seen`, `pulse_opportunities` trong `resonance.sqlite3` | mục 5, 6, 10 phần nhịp |
| J (phán xử) | `server/agent_judge.py` (thuần), `server/agent_judge_store.py` | kho mới `agent_judge.sqlite3` | mục 7, 10 phần phán xử, 9 (`judge_daily`, `K`) |
| L (học) | `server/agent_lessons.py`, `server/agent_review.py` (lượt xem xét reaction 4.4, tự rà soát 8.3), `server/agent_budget.py` | `agent_lessons`, `agent_lesson_events`, `agent_peer_feedback` trong `resonance.sqlite3` | mục 4.4, 8, 6.4, 9 (`agent_daily_calls`), 10 phần bài học |

Mỗi module tự tạo bảng của mình (`CREATE TABLE IF NOT EXISTS` trên cùng đường dẫn kho) để ba phần không cùng sửa một khối schema.
Chỗ nối vào `main.py`, `team_runner.py`, `resonance_heartbeat.py` giữ ngắn: một lời gọi tới hàm công khai dưới đây.

## 1. Nhịp (P) cung cấp

- `agent_pulse.record_signal(store, *, source_ref, agent_key, context_ref, kind, value, w, share=1.0, reactor="", emoji="",
  reasons=(), at=None) -> bool`: ghi `pulse_signals`, idempotent theo `(source_ref, agent_key)`. Bộ chiếu T2 gọi hàm này ở móc
  hiện đang là no-op. Reaction của agent không bao giờ gọi.
- `agent_pulse.supersede_signal(store, source_ref, by_ref)` và `void_signal(store, source_ref)`.
- `agent_pulse.wake(store, agent_key, context_ref, why, at=None)`: thức lại theo 5.2 (`min(bậc, B3)`), dùng cho lời gọi trực tiếp, giao
  việc mới, reaction mới hợp lệ, sự kiện liên quan.
- `agent_pulse.set_state(store, agent_key, context_ref, state, by)` cho `paused`/tiếp, `waiting_owner`, `off` (bật/tắt Cộng hưởng gọi
  qua đây và ghi `resumed_at`).
- `agent_pulse.view(store, agent_key) -> dict` cho `GET /agents/{slug}/pulse`.
- `agent_pulse.apply_policy_change(store, agent_key, context_ref, change)` / `revert_policy_change(...)`: phiên bản chính sách và cửa sổ
  quan sát 6.4 (bộ học L gọi khi tự áp, bộ phán xử J gọi khi nhãn dịch ngưỡng).
- Lần thức (P sở hữu): bước code, danh sách cơ hội, lượt `scan` với đầu ra `{"action": "none"|"propose"|"enqueue"|"say", ...}`. Hành
  động `say` gọi `agent_judge.judge_proactive(...)` (J) trước khi gửi.

### 1b. Chỗ cắm đã có ở phần P (chi tiết: `docs/dev/team-chat-t3-pulse.md` mục 7)

- `agent_pulse_host.register_view_part(name, fn(store, agent_key))`: mục của J/L trong `GET /agents/{slug}/pulse` (khoá `parts`);
  phía trang `window.JavisPulseParts.push({name, render(view, null, {slug})})` (`agent-pulse.js` gọi `render(view, null, {slug})`).
- `agent_pulse_host.register_reason_handler(code, fn(host, reason))`: phục vụ `reaction_review` (L), `related_review` và
  `wait_unblocked` (J); hàm tự `WakeReasons.claim(..., try_busy, release_busy)` với `host.busy_pair(agent_key)`.
- `judge_proactive(..., origin="pulse", payload={"judge_daily", "source_ref"})`: host KHÔNG truyền `engine` (bộ phán xử dùng đường
  hỏi model của chính nó, sửa review T3 J P1: trước đây host truyền một lambda không đối số, sai chữ ký, mọi `say` thành im).
  `judge_daily` lấy qua `runner._judge_caps` (Toàn quyền đè đúng). Host gọi `agent_budget.try_spend(agent_key, "scan", at)` trước khi
  giữ chỗ lượt quét.
- Bộ chiếu: `FeedbackProjector.record_task_event(event, context_ref=, agents={key: share}, source_ref_=)`, `pulse_wake`,
  `void_task_event`.

## 2. Phán xử (J) cung cấp

Chi tiết và lý do: [team-chat-t3-judge.md](team-chat-t3-judge.md). Ba hàm gọi model là coroutine (`async`).

- `await agent_judge.judge_team_message(ctx) -> list[Decision]`: lọc bằng code rồi một lô (purpose `judge_batch`), `K` người tự lên
  tiếng. `team_runner` gọi sau các lượt trả lời của tin chủ nhóm (`run_message`) và sau `_accept` (lượt xem xét liên quan 7.5).
- `await agent_judge.judge_proactive(*, agent_key, context_ref, draft, source_event, engine=None, origin="", payload=None) ->
  Decision`: nói/chờ/im cho tin chủ động ở chat riêng và nhóm. Lỗi, hết giờ, sai cấu trúc: `silent` có lý do, không gửi. Trong
  nhóm truyền `payload={"judge_daily": <trần>}`. `engine`: `async (prompt, purpose, started) -> str`, mặc định model việc nền.
  Dưới chốt test `JAVIS_NO_REAL_JUDGE_ENGINE=1`, mọi hàm hỏi (kể cả `engine=` và `ctx["ask"]`) bị từ chối trừ hàm đánh dấu
  `agent_judge.mark_test_fake`. Lỗi trước `started()` hoàn dòng sổ (`engine_unavailable`). Ở chat riêng, `origin =
  "reaction_review"` tiêu hạn mức ở mức ưu tiên `reaction_review`.
- `Decision = {"decision_id", "verdict": "speak"|"wait"|"silent", "score", "threshold", "reason", "wait_until", "ask_clarify",
  "silence_code", "kind", "volunteer", ...}`.
- `agent_judge.record_direct(agent_key, context_ref, source_event, *, root_ref="", excerpt="", message_ref="")`: ghi quyết định
  `address=certain` cho lời gọi trực tiếp (không model).
- `agent_judge.label(decision_id or "msg:<id>", label, by, *, at=None, source="history")` với `label` là `should_speak`/
  `should_silent` (rỗng là bỏ): dịch độ lệch theo ngữ cảnh (±0.25, nửa đời 14 ngày), thêm ca; chỉ khi Cộng hưởng bật và nhãn sau
  `resumed_at` mới đổi ngưỡng. Trục `axis` của reaction T2 gọi hàm này qua bộ chiếu (`source="reaction"`).
- `agent_judge.set_offset(agent_key, context_ref, value, source)`, `set_eagerness(agent_key, value, source)`, `get_profile(agent_key)`:
  bộ học L gọi khi tự rà soát áp `offset_set`/`eagerness_set`.
- `agent_judge.release_waits(context_ref, event) -> list`: gỡ chờ, tạo lý do `wait_unblocked`; gọi từ `_settle_reply_out`, chuyển
  trạng thái việc, vòng 5 giây của nhóm (`event.kind` = `owner_message`|`speaker_done`|`job_change`|`tick`).
- `agent_judge.decisions(agent_key, context_ref=None, limit=50)` cho lịch sử quyết định và nút sửa; `agent_judge.view(agent_key,
  contexts)` cho phần "judge" của khung nhịp.
- `await agent_judge.serve_reason(reason_id)`: P chuyển lý do `related_review`/`wait_unblocked` cho J (không tự chạy lượt).
- `agent_judge.register_sender(fn)`: P/L đăng ký cách gửi tin chủ động khi một lần chờ được gỡ và xét lại thành `speak`,
  `fn(decision, payload, mode)` với `mode` = "send"|"ask_clarify". Với "ask_clarify" người gửi KHÔNG đăng nguyên văn
  `payload["ask_clarify"]` (chữ của model phán xử); host gửi câu cố định của mình (`main._judge_clarify_template`).

## 3. Học (L) cung cấp

Chi tiết và lý do: [team-chat-t3-learn.md](team-chat-t3-learn.md).

- `agent_lessons.add(store, *, agent_key, scope, kind, text, source, source_refs=(), at=None) -> id`,
  `revoke(store, lesson_id, *, by, reason="", agent_key=None) -> dict|None`, `promote(store, lesson_id, *, by ("owner"|"review"),
  evidence_contexts=(), source_refs=()) -> id_mới`, `restore(...)`, `for_prompt(store, agent_key, scopes) -> list`,
  `scopes_for(context_ref, agent_key)`, `prompt_block(store, agent_key, context_ref) -> str`: lượt nhóm nạp `team:<id>` và `*`, chat
  riêng nạp `dm:<agent_key>` và `*`. `agent_lessons.TeamLearning(store_fn, task_state=...)` là phụ thuộc `learning` của `TeamRunner`.
- `agent_review.ReviewHost(ReviewDeps(...))`, nối một lần bằng `agent_review.wire(host)` rồi `agent_review.register_with_pulse()`
  (bộ xử lý `reaction_review`, mục "lessons", hàm quay lại cho `offset_set|eagerness_set|lesson_add|lesson_remove|lesson_promote`).
  `ReviewHost.tick(agents)` chạy trước `PulseHost.tick`. `ReviewHost.on_judge_send(decision, payload, mode)` là bộ gửi đăng ký với J
  (chỉ nhận `payload.origin == "reaction_review"`).
- `JAVIS_LESSON` của chat riêng ghi `agent_lessons` scope `dm:<agent_key>` (không còn vào `MEMORY.md`); của lượt nhóm scope
  `team:<id>`; `JAVIS_RULE` giữ nguyên.
- `agent_review.serve_reaction_review(reason)`: phục vụ lý do `reaction_review` theo 4.4 (lượt có cấu trúc, `consumed ⊆ nguồn`, trạng
  thái theo kết quả host thật, ghi `agent_pulse_events`).
- `agent_review.maybe_self_review(agent_key)`: 8.3, tối đa 3 thay đổi từ danh sách đóng, tự áp, ghi trước/sau, quay lại theo 6.4.
- `agent_budget.try_spend(agent_key, purpose, at=None, *, store=None, reason_ref=None) -> (ok, reason)` (chỉ kiểm, đếm từ
  `agent_calls`; `main.py` nối kho bằng `agent_budget.wire(store_fn)`): trần `agent_daily_calls` (mặc định 6, chủ có "Không giới hạn"),
  thứ tự ưu tiên `reaction_review` > `related_review` > `wait_unblocked` > chủ động/quét, giữ 2 lượt cho hai loại đầu. Mọi lượt của P, J
  (trừ lô phán xử nhóm, tính vào `judge_daily`) và L hỏi hàm này trước khi giữ chỗ sổ lượt. Hết trần: lý do giữ `pending`, trạng thái
  "Chờ hạn mức" (`waiting_quota`).
- Lệnh `{"op": "peer_feedback", "message_id": .., "note": ..}` trong lượt agent (host gán nguồn), khối "Góp ý của đồng đội" trong lượt
  kế tiếp đúng nhóm.

## 4. Giao diện

- `GET /agents/{slug}/pulse` (P dựng khung, J và L góp phần của mình qua `view` của từng module): trạng thái và bậc theo ngữ cảnh, lần
  xem xét gần nhất, lần dự kiến, lý do thức, đổi nhịp gần đây, phản hồi đã xử lý, bài học theo phạm vi với nút thu hồi (và nhãn "bài học
  cũ chưa có phạm vi"), quyết định nói/chờ/im gần đây với nút sửa.
- Route hành động: `POST /agents/{slug}/pulse/wake|pause|resume|reset`, `POST /agents/{slug}/pulse/revert` (P);
  `POST /agents/{slug}/judge/label` (J); `POST /agents/{slug}/lessons/{id}/revoke` (L). Chỉ hiện thành công sau khi host trả 200.
- `POST /seen {context_ref, message_ids}` (P) và phía trang: tin của agent nằm trong khung nhìn ≥ 1,5 giây khi tab hiển thị.
- Bảng giới hạn T1 thêm `judge_daily`, `K`, `agent_daily_calls`, `max_tier`, dự trữ 20%.
