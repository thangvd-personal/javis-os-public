# T3 phần J: bộ phán xử nói / chờ / im của từng agent (0.156.0)

Thiết kế: [2026-10-15-team-adaptive-design.md](../superpowers/specs/2026-10-15-team-adaptive-design.md) mục 2, 3, 7, 9, 9.1,
10, 12, 13 (ca 1, 2, 10, 17 phần phán xử, 19, 20, 21, 27). Hợp đồng ba phần: [team-chat-t3-contract.md](team-chat-t3-contract.md)
mục 2 và phần phán xử của mục 4. Phần P (nhịp thích nghi) và phần L (học) làm song song, gặp nhau ở hợp đồng.

## 1. Kiến trúc

| Phần | File | Việc |
|---|---|---|
| Chính sách thuần + dịch vụ | `server/agent_judge.py` | Cổng code (`prefilter`), dựng prompt lô và prompt tin chủ động, đọc JSON có kiểm (`parse_batch`, `parse_one`), chọn K người (`select_volunteers`), dịch ngưỡng (`label_delta`, `threshold_for`). Lớp `Judge` nhận mọi phụ thuộc qua tham số (kho, sổ lượt, lý do thức, engine, cờ Cộng hưởng, lịch sử ngữ cảnh, `try_spend`). Hàm cấp module theo hợp đồng gọi `Judge` mặc định do `main.py` nối (`agent_judge.wire`). |
| Kho | `server/agent_judge_store.py` | Kho mới `agent_judge.sqlite3`: `profiles`, `decisions`, `offsets`, `cases`, `waits`, `changes`, `review_state` (mục 7.1) và `chains` (chuỗi tin trong nhóm để chặn độ sâu và A->B->A). Mở lười: chưa có lượt phán xử nào thì không có file. |
| Bộ chạy nhóm | `server/team_runner.py` | `submit` ghi gợi ý "sao không trả lời", gỡ chờ `owner_message`, đánh dấu `judge`; `run_message` = lượt trả lời theo @ rồi `run_judge`; `_run_volunteers` (lượt `member_volunteer`); `_judge_after_accept` (chuỗi tin + xem xét liên quan); `_judge_team_waits` (xét lại, hết hạn); lệnh `ask_peer`; gỡ chờ ở `_settle_reply_out` và mọi lần chuyển trạng thái việc; `judge_tick`. |
| Sổ lượt | `server/agent_ledger.py` | `member_volunteer` là lượt nhóm TỰ PHÁT (obligation 0, chỉ dùng tới 80% trần chung). |
| Kho nhóm | `server/team_store.py` | Trần mới `judge_daily` (40, có Không giới hạn), `volunteer_k` (K, 1, tối đa 5, 0 tắt), `judge_off_batch` (1 = lọc code + lô, 0 = chỉ lọc code); vai lượt `member_volunteer`. |
| Bộ chiếu phản hồi | `server/feedback_projector.py` | Trục `axis` của reaction CỦA CHỦ tới `agent_judge.label` (`record_judge_label`, nguồn `reaction`). |
| Host | `server/main.py` | `_agent_judge()` (engine phán xử = model việc nền người dùng chọn, thư mục trống, không công cụ), gỡ chờ khi chủ nhắn chat riêng, route `GET /agents/{slug}/judge`, `POST /agents/{slug}/judge/label`, góp phần "judge" vào khung nhịp của phần P. |
| Vòng nền | `server/team_api.py` | Gửi tin chạy `run_message` nền; vòng 5 giây gọi `judge_tick` (cả khi chưa có kho nhóm, cho chat riêng). |
| Giao diện | `dashboard/agent-judge-ui.js`, `teams.js`, `console.css`, i18n | Lịch sử quyết định theo ngữ cảnh (cả ca im không tin) với nút sửa; ô trong hàng thành viên nhóm; bảng giới hạn và ô giới hạn của chủ; nút gợi ý dưới tin "sao không trả lời". |

## 2. Luồng

### 2.1 Chat nhóm (7.2)

1. Chủ gửi tin. `submit` định tuyến như cũ (tin có @ gọi người được @, tin thường gọi ô điều phối). Lời gọi trực tiếp và việc giao
   (`as_job`) ghi quyết định `kind=direct, address=certain` (không model, không ngưỡng). Route trả ngay.
2. Nền: `run_message` chạy lượt trả lời theo @ trước (để bộ phán xử thấy câu đã trả lời, tránh nói trùng), rồi `run_judge`.
3. Cổng code cho từng thành viên đang hoạt động KHÔNG được gọi: việc mở có chữ trùng tin, câu hỏi chung cho nhóm, từ khoá vai, đóng
   góp liên quan trong 30 phút. Không tín hiệu: `silent/no_signal`, không model. Agent tắt Cộng hưởng ở chế độ "chỉ lọc code":
   `silent/code_only`.
4. Ứng viên còn lại vào MỘT lô (tối đa 8): giữ chỗ MỘT dòng `agent_calls` purpose `judge_batch`, `agent_key` rỗng, `context_ref`
   nhóm, obligation 0, counted 1, TRƯỚC engine; trần `judge_daily` đếm và giữ chỗ trong CÙNG giao dịch BEGIN IMMEDIATE. Tối đa 2
   lô đồng thời trong tiến trình, hạn 25 giây. Mỗi quyết định trỏ `ledger_ref = call:<id>`.
5. Điểm >= ngưỡng riêng của agent (hăng hái low 0.75 / medium 0.60 / high 0.45 cộng độ lệch theo ngữ cảnh, kẹp 0.30-0.90) mới là
   muốn nói. Chọn tối đa K người điểm cao nhất (hoà thì theo thứ tự thành viên: ổn định, không round-robin), trừ số người đã tự
   lên tiếng cho cùng tin gốc. Người khác: `not_selected`; K = 0: `k_zero`.
6. Người được chọn chạy lượt `member_volunteer` qua đúng đường lượt nhóm (`team_turns` + sổ chung, khoá bận, trần ngày, dòng kết
   cục `team_replies` có Thử lại). Không lệnh quản trị. Không đăng được tin thì quyết định ghi lại `turn_blocked`.

### 2.2 Chat riêng và tin chủ động (7.3)

Câu hỏi trực tiếp của chủ đi luồng trả lời thường, không qua phán xử; host chỉ gỡ các lần chờ `owner_message` của `dm:<agent_key>`.
`judge_proactive(...)` dành cho tin chủ động (lượt quét `say` của P, `ask_back`/`revise` của L). Ở chat riêng lượt phán xử hỏi
`agent_daily_calls` trước (bộ chuyển `try_spend`, mục 5), rồi giữ chỗ một dòng `judge_batch` có `agent_key`. Trong nhóm lượt tính
vào `judge_daily` (người gọi truyền `payload.judge_daily`). Lỗi, hết giờ, JSON sai, engine chưa sẵn sàng: `silent` có mã, không gửi.

### 2.3 Chờ và gỡ chờ (7.4)

`wait` ghi `waits` với `until_kinds` (`owner_message`, `speaker_done`, `job_change`), `until_ts = lúc quyết + 30 phút`, `job_refs`
(việc mở của agent). Gỡ ở: `submit` (tin mới của chủ), `_settle_reply_out` (người đang nói chốt lượt), `_block`, `_finish`,
`_cancel_records`, `unblock`, lúc nhận việc (việc đổi trạng thái), vòng 5 giây (hết hạn), lượt chat riêng của chủ. Gỡ là CAS
`waiting -> released` kèm một lý do `wait_unblocked` (nguồn `wait:<id>`). Xét lại đúng một lần mỗi (agent, tin gốc): xét lại mà
vẫn chọn chờ thì `rejudge_limit`. Hết hạn: có `ask_clarify` thì một lượt của CHÍNH agent hỏi trưởng nhóm/chủ đúng một câu
(`kind=clarify`), không thì `silent/wait_expired`. Lần chờ đã gỡ mà chưa xét lại quá 120 giây (mất việc nền) được vòng tick xét lại.

### 2.4 Xem xét liên quan (7.5)

Thay điều khoản T1 "tin của agent không đánh thức ai" bằng hợp đồng có kiểm soát: sau `_accept` đăng tin của agent X (còn hiệu
lực), host ghi chuỗi tin (tin gốc của chủ, độ sâu, ai đã đánh thức X) rồi tính người nhận CHỈ từ quan hệ có cấu trúc: người nhận
và điều phối việc của tin, bước có `input_refs` trỏ tin, người đã nhờ hỗ trợ (bước cha của bước `help`), lệnh
`{"op": "ask_peer", "agent_key": ..}` (host kiểm người nhờ là agent của lượt, người được nhờ là thành viên khác). Chặn: tự gọi
mình, chữ `@` trong văn bản (không bao giờ là quan hệ), trích dẫn (bỏ trước khi phán xử), độ sâu > 2, quá một lượt mỗi (agent, tin
gốc), A->B->A, trần `judge_daily`. Người qua được nhận lý do `related_review` và đi qua bước 3-6 của 2.1 (một lô cho mọi người nhận
của tin X, K tính theo tin X).

### 2.5 Dạy phán xử (7.6)

`label(quyết định hay msg:<id>, should_speak|should_silent|rỗng, by, at, source)`: nhãn trái quyết định dịch độ lệch của ĐÚNG ngữ
cảnh (`should_speak` trên im: -0.05 x 1.5; `should_silent` trên nói: +0.08 x 1.5), kẹp ±0.25, nửa đời 14 ngày; nhãn đồng ý chỉ thêm
ca. Hiệu lực ghi ở `label_effect`: `applied` chỉ khi Cộng hưởng bật và nhãn sau `resumed_at`; `stored_only`,
`stored_before_resume`, `stored_direct` (lời gọi trực tiếp không bao giờ dạy phán xử). Đổi nhãn hoàn phần cũ trước. Bỏ trục trên
reaction không xoá nhãn chủ sửa trong lịch sử. Không đổi ước lượng nội dung. Tin "sao em không trả lời?" chỉ thêm gợi ý
`judge_hint` (quyết định im gần nhất trong 24 giờ) lên tin của chủ; xác nhận là sửa trong lịch sử quyết định.

## 3. Prompt (gửi model, giữ tiếng Việt)

- Lô nhóm (`build_batch_prompt`): vai của bộ phán xử (không viết câu trả lời), từng người cần xét kèm vai, hăng hái, tín hiệu code
  và tối đa 3 ca CÙNG ngữ cảnh, người đã được gọi, 12 tin gần nhất CỦA NHÓM NÀY trong `<chat_data>`, tin cần xét, nguyên tắc (chỉ
  nói khi có đóng góp riêng, không ai phải nói cho đủ lượt), khuôn JSON
  `{"decisions":[{"agent_key","verdict","score","reason","wait_for","ask_clarify"}]}`. Mã không nằm trong lô bị bỏ.
- Tin chủ động (`build_proactive_prompt`): vai, ca cùng ngữ cảnh, lịch sử của đúng ngữ cảnh, bản nháp, nguồn, khuôn JSON một mục.
- Lượt tự lên tiếng: "Không ai gọi bạn trong tin này; bạn tự lên tiếng vì có đóng góp riêng (...). Chỉ nói phần đóng góp đó".
  Câu trả lời luôn do agent tạo; lời giải thích của bộ phán xử chỉ là lý do trong quyết định.

## 4. Lỗi và cách xử

| Lỗi | Kết quả | Sổ lượt |
|---|---|---|
| Engine chưa sẵn sàng (ném trước khi gọi model) | `silent/engine_unavailable` | `refunded` |
| Lỗi engine sau khi gọi | `silent/policy_error` | `error` (tính) |
| Quá 25 giây | `silent/timeout` | `error` (tính) |
| JSON sai khuôn, thiếu người | `silent/bad_json` / `policy_error` | `ok` (model đã trả lời) |
| Chờ chỗ (2 lô) quá 25 giây | `silent/judge_busy` | `refunded` |
| Hết `judge_daily` / trần chung (80% cho lượt tự phát) | `silent/judge_cap` / `server_ceiling` | không giữ chỗ |
| Hết `agent_daily_calls` (chat riêng) | `silent/waiting_quota` | không giữ chỗ |
| Gọi lại lô của cùng tin | trả quyết định cũ | `ReasonBusy`, không lượt thứ hai |
| Mất việc nền khi gỡ chờ | vòng tick xét lại sau 120 giây | |
| Kho phán xử lỗi trong `_accept`/`submit` | ghi log, lượt nhóm và tin của chủ không hỏng | |

Lời gọi trực tiếp không bao giờ phụ thuộc phán xử: lượt trả lời chạy trước, lỗi phán xử chỉ làm ứng viên im.

## 5. Điểm nối với phần P và L

- P gọi `agent_judge.judge_proactive(...)` trước khi gửi tin `say` của lượt quét (truyền `payload={"judge_daily": ...}` nếu ngữ
  cảnh là nhóm). Kết quả `wait` thì P KHÔNG gửi; khi gỡ chờ, bộ phán xử xét lại và gọi người gửi P đăng ký bằng
  `agent_judge.register_sender(fn)` với `fn(decision, payload, mode)`, `mode` = "send" | "ask_clarify".
- P phục vụ lý do `related_review` và `wait_unblocked` bằng `agent_judge.serve_reason(reason_id)` (bộ phán xử tự xét lại; lý do của
  nhóm đã được chính lô phán xử khép `served`, nên thường không còn gì). P không chạy lượt riêng cho hai loại này.
- P đăng ký khung nhìn: `main._register_judge_view_part()` gọi `agent_pulse_host.register_view_part("judge", fn)` khi module có
  (nạp lười, không có thì bỏ qua); `fn(agent_key)` trả `Judge.view(...)`. Phía trang: `window.JavisPulseParts` có phần
  `{name: "judge", render(part, el, {slug})}`.
- P và J dùng chung `agent_pulse.apply_policy_change(store, agent_key, context_ref, change)` (nạp lười, `agent_pulse.default_store()`
  nếu có): J gọi khi nhãn dịch ngưỡng, khi `set_offset`, `set_eagerness`.
- L gọi `agent_judge.set_offset`, `set_eagerness`, `get_profile`, `decisions`; đọc/ghi `review_state` qua
  `default().store().last_review_at / set_review_at`.
- L cung cấp `agent_budget.try_spend(agent_key, purpose)`; J gọi qua `_default_try_spend` (nạp lười). Chưa có module: cho phép và
  lượt vẫn ghi vào sổ lượt chung (đó là nơi L đếm); test tiêm `try_spend` giả. Mục đích J dùng: `proactive_judge`, `wait_unblocked`.
- L viết `ask_back`/`revise` trong nhóm: gọi `judge_proactive` với `context_ref = team:<id>` như P.

## 6. Di trú và quay về

- Kho mới `agent_judge.sqlite3`, tạo ở lần dùng đầu. Không có `.pre-0.156.0` (trước đó không có kho). Kho cũ không đổi schema:
  `team.sqlite3` chỉ thêm khoá trong `caps_json` (bản cũ mang theo khoá lạ trong `caps` nhưng không đọc tới) và vai
  `member_volunteer` trong `team_turns` (bản cũ chỉ đọc chuỗi vai để hiển thị).
- Quay về: `tools/team_adaptive_restore_pre.py --to 0.156.0` (hay `--to 0.154.0`) cất `agent_judge.sqlite3` sang bên thành
  `.post-*` rồi gỡ khỏi chỗ chạy (dòng `absent` trong `MILESTONES`). J không thêm bảng nào vào `resonance.sqlite3`; dòng
  `judge_batch` và lý do `related_review`/`wait_unblocked` nằm trong bảng có sẵn của T2.
- Hạ phiên bản không khôi phục vẫn chạy: bản cũ không biết kho phán xử; quyết định cũ nằm yên đó.

## 7. Test

`tests/python/test_agent_judge_team.py`, `test_agent_judge_dm.py`, `test_agent_judge_labels.py` (kèm route qua `main.py`, chốt
engine thật), `test_related_review.py`, `test_agent_judge_scope.py`, `test_agent_judge_waits.py`; `tests/js/test_agent_judge_ui.js`.
Thế giới dùng chung `tests/python/_judge_world.py` (kho thật, engine giả). Sửa kèm: `test_team_t1_goal.py` (judge_daily có Không giới
hạn), `tests/js/test_teams_ui.js` (hộp cấp quyền 6 dòng trần).

## 8. Quyết định khi làm

- **Hàm cấp module là coroutine**: `judge_team_message`, `judge_proactive`, `serve_reason` là `async` (lời gọi model). Hợp đồng
  đã sửa cùng commit.
- **Engine phán xử nhận `started`**: `ask(prompt, purpose, started)`; `started()` ghi mốc engine ngay trước lời gọi model để hoàn
  đúng khi engine chưa dựng được.
- **Lý do của nhóm do lô khép**: `related_review`/`wait_unblocked` của nhóm được chính lô phán xử đánh `served` (kể cả khi lô lỗi
  hay hết trần), vì lỗi phán xử nhóm phải ra im chứ không thử lại ngoài ý chủ. Chat riêng dùng `WakeReasons.claim/settle` đầy đủ
  (thử lại theo vòng đời T2).
- **Lượt tự lên tiếng là tự phát** trong trần chung (obligation 0); vẫn tính `daily_turns` của nhóm như mọi lượt nhóm.
- **Chế độ khi Cộng hưởng tắt** đặt theo nhóm (`judge_off_batch` trong trần của nhóm, chỉ chủ chỉnh), vì `judge_daily` cũng theo nhóm.
- **K** tối đa 5, không có Không giới hạn; Toàn quyền chỉnh trong trần tối đa của chủ. `judge_daily` theo luật D6 như trần lượt
  model (lô chạy model việc nền "aux").
- **Ngưỡng khi Cộng hưởng tắt**: độ lệch đã học vẫn áp (chính sách đóng băng, vẫn nhạt dần theo nửa đời), nhãn mới chỉ lưu.
- **Bảng thêm**: `decisions` có `kind`, `root_ref`, `volunteer`, `message_ref`, `features_json`, `excerpt`, `ask_clarify`,
  `label_effect`, `label_delta`; `waits` có `root_ref`, `source_event`, `job_refs_json`, `payload_json`, `reason_id`,
  `release_event`; bảng `chains`. Không thu hẹp cột nào của mục 7.1.
- **Ca lưu đoạn trích ngắn** (<= 200 ký tự, trong `features_json`) để làm ví dụ trong prompt; chỉ đọc trong đúng ngữ cảnh.
- **Có thêm `GET /agents/{slug}/judge`** để hàng thành viên nhóm tải đúng một ngữ cảnh, không phụ thuộc phần P; khung nhịp của P
  dùng cùng `Judge.view`.
- **Dọn quyết định**: mỗi giờ bỏ quyết định im cũ hơn 90 ngày không nhãn, không tin, không đang chờ.

### 8.1 Sửa sau review T3 (nhánh `claude/team-adaptive-t3-fix`)

- **Chốt test không có đường vòng (P2-2)**: `Judge._run_engine`/`_run_engine_claimed` từ chối mọi hàm hỏi khi
  `JAVIS_NO_REAL_JUDGE_ENGINE == "1"`, trừ hàm `mark_test_fake`; biến đặt ở `tests/run.py`, `_paths.py`,
  `test_adaptive_context_phase8.py` và pilot A5 dry.
- **Lượt `say` của nhịp (P1)**: không truyền `engine`, có `payload` (`judge_daily` của nhóm). Lỗi ném trước `started()` hoàn dòng sổ
  và ra `engine_unavailable` (không tính trần).
- **Câu hỏi làm rõ ở chat riêng (P2-3)**: host gửi câu cố định; chữ `ask_clarify` của model chỉ nằm trong lịch sử quyết định. Ở
  nhóm, gợi ý vào lượt `member_volunteer` của chính agent trong khối `<chat_data>`.
- **Thử lại lượt tự lên tiếng (P2-4)**: `demote` giữ `features.volunteer_origin`; Thử lại chạy lại đúng quyết định (giữ `clarify`),
  thành công thì `restore_volunteer` (về `speak`, gắn tin).
- **P3**: nhãn lưu phần dịch đã áp sau kẹp; xét lại chat riêng nhận lý do trước rồi mới hỏi hạn mức (không đủ thì hoàn); lần chờ đã
  gỡ mà quá 30 phút thì hết hạn ở `tick`; lý do được khép ở đường gọi trùng, nhóm tạm dừng, lần chờ bị huỷ; ca đã học nằm trong
  `<chat_data>`, lượt tự lên tiếng không mang `reason` của bộ phán xử; "một lượt mỗi (agent, tin gốc)" giữ thêm bằng sổ trong tiến
  trình có khoá.
- **Lần chờ của tin chủ động trong NHÓM** (vòng 2 P2-1): lần chờ có bản nháp và không có `message_id` được xét lại đúng một lần như ở
  chat riêng (`_proactive_wait`): CAS `released -> rejudged`, khép lý do, `judge_proactive(rejudge_of=...)` với trần `judge_daily`
  của nhóm (không hỏi hạn mức ngày của agent), `speak` thì gửi qua `_judge_send` -> `_pulse_post` (đã kiểm phạm vi). Lần chờ của lô
  nhóm mà nhóm hay tin gốc không còn: quyết định `silent/wait_cancelled` nhìn thấy được.

## 9. Bằng chứng (nhánh `claude/team-adaptive-t3-judge`, head kiểm `1049027e`)

- `tests/run.py judge related team feedback agent_calls i18n icons route_table`: 43/43 xanh (439 giây).
- `tests/run.py --js`: 193/193 xanh (76 giây).
- **Sự cố đã khắc phục:** trước `1049027e`, một harness qua `main.py` (`test_team_t1_goal_host.py`, chạy lúc nối host mới xong)
  dựng engine phán xử thật và gọi Claude CLI MỘT lần (claude-opus-5-5, 122 token ra, 15/10/2026 11:16 +07). Nguyên nhân: harness
  cũ chỉ thay engine lượt nhóm, còn lô phán xử dùng model việc nền. Sửa: `tests/python/_paths.py` đặt
  `JAVIS_NO_REAL_JUDGE_ENGINE=1` cho mọi test Python, `main._agent_judge_ask` ném `EngineUnavailable` trước khi dựng engine (lượt
  được hoàn, ứng viên im); kiểm hồi quy trong `test_agent_judge_labels.py`. Hai lượt chạy đầy đủ sau đó không tạo thư mục
  `agent_judge_cwd` nào mới.
- Cùng lúc sửa: `team_runner.run_io` quay mãi khi chính tác vụ luồng phụ bị huỷ lúc event loop đóng (treo `test_team_api`);
  `TeamRunner.wait_jobs` chờ cả việc nền của bộ phán xử.


## 10. Hiệu năng

Lô phán xử không còn đọc/ghi kho trên event loop (phần chuẩn bị và phần chốt chạy qua `run_io`, chỉ lời gọi model chờ trên
loop; huỷ ngay sau giữ chỗ thì hoàn dòng sổ). Chi tiết, số đo trước/sau và test: [team-chat-t3-pulse.md](team-chat-t3-pulse.md)
mục 9.
