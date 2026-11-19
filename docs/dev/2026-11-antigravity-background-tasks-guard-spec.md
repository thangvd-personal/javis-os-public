# Đặc tả kỹ thuật: Cơ chế phòng ngừa treo lượt và dọn dẹp tác vụ nền cho Antigravity CLI

Tài liệu đặc tả kỹ thuật (Spec) viết ngày 19/11/2026.
Vị trí tài liệu: `docs/dev/2026-11-antigravity-background-tasks-guard-spec.md`.

---

## 1. Bối cảnh và Vấn đề

Trong quy trình làm việc nhóm (Team Work) của Javis OS, các agent thực hiện công việc kỹ thuật nặng (như kiểm thử E2E, chạy bộ testsuite lớn, kiểm tra bao phủ mã nguồn) thường sử dụng các công cụ tạo tác vụ chạy nền hoặc hẹn giờ (`schedule`, `run_command` dạng bất đồng bộ) để theo dõi tiến trình kiểm thử.

### Triệu chứng sự cố
Tại bước **Nghiệm thu độc lập Chặng 2: API đọc IAM** (`run_c53021ecc0cf40`), agent *Người chạy test E2E* (`gemini-3.8-flash-medium` chạy qua `antigravity-cli`) đã chạy hoàn tất toàn bộ 101/101 test, lập biên bản nghiệm thu độc lập đạt chuẩn và xuất khối hoàn thành:
```html
<!-- JAVIS_TEAM: {"op": "done", "outcome": "succeeded", ...} -->
```
Tuy nhiên, ngay sau đó bước công việc bị chuyển thành `team_blocked` với thông báo lỗi:
```text
Người chạy test E2E gặp lỗi khi làm: engine báo lỗi (root agent idle; waiting up to 720h0m0s for 2 background task(s))
```

### Phân tích nguyên nhân gốc rễ (Root Cause)
1. **Trạng thái treo chờ tác vụ nền mồ côi của binary `agy`:**
   - Trong quá trình chạy test, agent đã tạo các tác vụ hẹn giờ qua `schedule` (ví dụ `task-122`, `task-132`...).
   - Trước khi gửi câu trả lời kết thúc lượt, agent quên gọi `manage_task(Action='kill')` để huỷ 2 tác vụ nền còn lại.
   - Khi chạy ở chế độ headless / in kết quả (`agy -p`), Antigravity CLI nhận thấy agent chính đã trả lời xong và chuyển sang trạng thái nhàn rỗi (`root agent idle`). Tuy nhiên, do còn 2 tác vụ nền trong bộ nhớ tiến trình, `agy` không tự thoát mà ghi ra stderr:
     ```text
     root agent idle; waiting up to 720h0m0s for 2 background task(s)
     ```
     và tiếp tục treo tiến trình để chờ các tác vụ nền với thời hạn tối đa lên đến `--print-timeout` (mặc định là `720h`).
2. **Lỗi đánh đồng stderr với mã lỗi trong `server/antigravity_cli.py`:**
   - Tại dòng 1782 cũ của `server/antigravity_cli.py`, điều kiện xử lý sau khi tiến trình kết thúc là:
     ```python
     elif ma != 0 or err:
         publish({"_exit": ma, "_err": err})
     ```
   - Ngay cả khi các tác vụ nền hẹn giờ ngắn (5s - 10s) kết thúc và tiến trình `agy` thoát với mã thành công (`ma == 0`), sự hiện diện của dòng thông báo chẩn đoán trên `stderr` vẫn khiến Javis coi đây là một lần thoát lỗi (`_exit: 0, _err: ...`).
3. **Phát sự kiện `error` làm hỏng kết quả hợp đồng lượt:**
   - Hàm `_doi_su_kien` nhận `_exit` có `_err` không rỗng và phát ra sự kiện `{"type": "error", "content": ...}` trước sự kiện `final`.
   - Lớp kiểm soát kết cục lượt `ToolTurnOutcome` trong `server/team_runner.py` ghi nhận lỗi này vào `self.error` và ném ra `EngineOutcomeError("error_event", "root agent idle...")`, làm đánh trượt oan toàn bộ kết quả kiểm thử hoàn hảo của agent.

---

## 2. Thiết kế giải pháp đa tầng (Multi-layer Prevention)

Để ngăn chặn triệt để hiện tượng này cho tất cả các agent sử dụng `antigravity-cli`, hệ thống triển khai giải pháp kết hợp 3 tầng bảo vệ:

### Tầng 1: Chỉ dẫn hệ thống nhắc nhở dọn dẹp tác vụ nền (Prompt Guard)
1. **Trong `server/aux_engine.py` (`_build_antigravity`):**
   Tự động bổ sung quy tắc quản lý tác vụ nền vào `instructions` của mọi engine `AntigravityCLI`:
   ```text
   [QUY TẮC QUẢN LÝ TÁC VỤ NỀN CHO ANTIGRAVITY]: Nếu bạn dùng các công cụ tạo tác vụ chạy ngầm hoặc hẹn giờ (schedule, run_command chạy nền), bạn PHẢI kiểm tra và huỷ (kill) hoặc đợi tất cả các tác vụ nền hoàn tất bằng manage_task trước khi kết thúc lượt. Tuyệt đối không để sót tác vụ nền treo khi kết thúc lượt.
   ```
2. **Trong `server/team_runner.py` (`WORK_TOOLS_NOTE`):**
   Bổ sung điều khoản tương ứng vào ngữ cảnh chuẩn cho tất cả các lượt làm việc có công cụ trong việc nhóm.

### Tầng 2: Cơ chế phát hiện Root Agent Idle & Thoát êm đẹp (Graceful Idle Exit)
Trong `server/antigravity_cli.py`:
- Khởi tạo 2 cờ kiểm soát luồng `da_xong_result` và `idle_seen`.
- Trong luồng đọc `stdout`: Khi nhận được sự kiện kết thúc có cấu trúc (`result`, `final`, `done`, `complete`) từ stream JSON hoặc bắt được thẻ `<!-- JAVIS_TEAM:` trong chế độ text thô, kích hoạt `da_xong_result.set()`.
- Trong luồng đọc `stderr` (`_doc_loi`): Khi phát hiện chuỗi `root agent idle`, kích hoạt `idle_seen.set()`. Đồng thời kích hoạt bộ dọn dẹp trễ 2.0 giây: nếu sau 2.0 giây mà tiến trình con vẫn còn chạy vì chờ tác vụ nền mồ côi, chủ động gọi `_kill_tree(proc)` để ngắt tiến trình, ngăn chặn việc treo lượt kéo dài.
- Nếu cả 2 cờ `da_xong_result` và `idle_seen` cùng thoả mãn, tiến trình con được ngắt ngay lập tức, luồng đọc `stdout` thoát êm đẹp mà không làm mất bất kỳ dữ liệu kết quả nào.

### Tầng 3: Sàng lọc stderr & Xử lý mã thoát chuẩn Unix
Trong `server/antigravity_cli.py`:
- **Đồng bộ triết lý với `grok_cli.py` (chuẩn Unix):**
  - Khi `proc.wait()` trả về `ma == 0`, việc `stderr` có chứa log chẩn đoán hoặc thông báo nhàn rỗi KHÔNG được coi là lỗi. Log được ghi lại vào `sys.stderr` để phục vụ debug, không phát sự kiện `_exit` lỗi.
  - Khi `da_xong_result.is_set()` hoặc `idle_seen.is_set()` bật: việc tiến trình bị ngắt để thu hồi tác vụ nền mồ côi được xác nhận là hành vi dọn dẹp hợp lệ, không đánh giá là lỗi của lượt.
- **Hàm nhận diện thông báo nền `_la_thong_bao_nen_vo_hai(loi: str) -> bool`:**
  - Nhận diện các chuỗi `root agent idle` và `background task(s)`.
  - Cả `_doi_su_kien` và `query()` đều chủ động bỏ qua các thông báo này, không phát sinh sự kiện `{"type": "error"}` gây gián đoạn hợp đồng `ToolTurnOutcome`.

---

## 3. Các tệp mã nguồn thay đổi

1. `server/antigravity_cli.py`:
   - Định nghĩa hàm `_la_thong_bao_nen_vo_hai(loi: str) -> bool`.
   - Bổ sung `da_xong_result` và `idle_seen` trong `_mot_luot()`.
   - Giám sát `root agent idle` trên `stderr` và tự động thu hồi tiến trình mồ côi sau khi đã nhận đủ câu trả lời.
   - Sửa điều kiện thẩm định `ma == 0` và bỏ qua thông báo nền trong `_doi_su_kien` và `query()`.
2. `server/aux_engine.py`:
   - Cập nhật `_build_antigravity`: tiêm `[QUY TẮC QUẢN LÝ TÁC VỤ NỀN CHO ANTIGRAVITY]` vào `instructions`.
3. `server/team_runner.py`:
   - Bổ sung chỉ dẫn quản lý và dọn dẹp tác vụ nền bằng `manage_task` vào hằng số `WORK_TOOLS_NOTE`.
4. `tests/python/test_antigravity_background_tasks_guard.py`:
   - Bộ kiểm thử tự động kiểm tra: nhận diện thông báo nền, bỏ qua lỗi khi thoát 0 có log stderr, bỏ qua lỗi khi bị kill lúc idle waiting, tiêm prompt instructions trong `aux_engine` và cập nhật `WORK_TOOLS_NOTE`.

---

## 4. Kết quả nghiệm thu

- Chạy kiểm thử tự động chuyên biệt:
  ```bash
  .venv/bin/python tests/python/test_antigravity_background_tasks_guard.py
  # ok   _la_thong_bao_nen_vo_hai matches standard agy idle background waiting message
  # ok   _la_thong_bao_nen_vo_hai matches root agent idle substring
  # ok   _la_thong_bao_nen_vo_hai matches background task(s) substring
  # ok   _la_thong_bao_nen_vo_hai rejects real errors
  # ok   _la_thong_bao_nen_vo_hai rejects empty string
  # ok   _doi_su_kien ignores exit code 0 with agy idle stderr
  # ok   _doi_su_kien ignores exit code 0 with generic stderr warning
  # ok   _doi_su_kien suppresses error event when killed during agy idle background wait
  # ok   _doi_su_kien emits error event for genuine command failure
  # ok   aux_engine._build_antigravity injects task cleanup instructions
  # ok   aux_engine._build_antigravity preserves original system prompt
  # ok   team_runner.WORK_TOOLS_NOTE mentions background task cleanup (manage_task kill)
  ```
- Kiểm thử hồi quy các module Antigravity CLI hiện có:
  ```bash
  .venv/bin/python tests/python/test_team_antigravity_coordinator.py
  .venv/bin/pytest tests/python/test_antigravity_stop.py tests/python/test_antigravity_chot_van.py
  # 100% passed.
  ```
- Từ nay, các agent sử dụng `antigravity-cli` khi tạo tác vụ ngầm/hẹn giờ sẽ không còn bị treo 720 giờ hoặc bị timeout supervisor báo lỗi `root agent idle; waiting up to...` làm gián đoạn tiến độ công việc.
