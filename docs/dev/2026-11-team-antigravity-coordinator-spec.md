# Đặc tả kỹ thuật: Ngoại lệ điều phối thuần văn bản cho Antigravity CLI trong việc nhóm

Tài liệu đặc tả kỹ thuật (Spec) viết ngày 19/11/2026.
Vị trí tài liệu: `docs/dev/2026-11-team-antigravity-coordinator-spec.md`.

---

## 1. Bối cảnh và Vấn đề

Trong hệ thống Javis OS, các lượt thực thi trong nhóm làm việc (Team) được chia làm 2 loại:
1. **Lượt Làm và Hỗ trợ (`tool_turn=True`)**:
   - Được phép gọi công cụ (tools) đầy đủ như một lượt chat riêng (`team_engine.build_tools`).
   - Cả Claude, Codex, Grok Build và Antigravity CLI đều được hỗ trợ đầy đủ.
2. **Lượt Nhận việc, Lập kế hoạch, Quét, Học và Điều phối (`tool_turn=False`)**:
   - Chạy ở chế độ thuần văn bản (text-only).
   - Yêu cầu engine không được kích hoạt công cụ (không gọi read_file, write_file, run_command hay MCP).

### Điểm nghẽn trước đây với Antigravity CLI

Trước đây, tại `server/team_engine.py:133-135`, hàm `build()` chỉ hỗ trợ cơ chế chỉ chữ cho:
- `anthropic-cli` (Claude hộp cát với `allowed_tools` rỗng).
- `openai-oauth` (Codex với cờ tắt `features.*` và sandbox read-only).
- Các API model qua gateway (`no_tools = True`).

Đối với `antigravity-cli`, do bản CLI `agy` chạy cục bộ chưa có cờ dòng lệnh dạng cứng như `--deny-all-tools` để tắt riêng từng công cụ native của CLI, hệ thống trước đây đã chọn giải pháp từ chối cứng:
```text
Không dựng được engine: antigravity-cli chưa chạy được lượt nhóm chỉ chữ (còn công cụ native không tắt riêng cho lượt này được). Đổi model của trợ lý ở trang Cộng sự sang Claude, ChatGPT (Codex) hay một model API
```

Hậu quả:
- Khi một tác vụ tự động ngầm (ví dụ: `[Javis GitHub] CI đỏ: Quality Checks & Unit/Integration Tests`) kích hoạt điều phối viên hoặc trợ lý nhóm sử dụng model Antigravity, tiến trình lập tức bị dừng và báo lỗi.
- Người dùng buộc phải đổi model của trợ lý sang Claude hoặc model API bên thứ ba, làm mất đi sự nhất quán khi người dùng muốn sử dụng `antigravity-cli` xuyên suốt cho toàn bộ hệ thống.

---

## 2. Thiết kế giải pháp: Cơ chế ngoại lệ 2 lớp

Để `antigravity-cli` tham gia trọn vẹn vào các lượt điều phối thuần văn bản mà không bị chặn, giải pháp kết hợp cả 2 phương án kiểm soát:

### Lớp 1: Ngoại lệ hạ tầng và cô lập môi trường
- Bổ sung `antigravity-cli` vào danh sách các provider hỗ trợ chế độ chỉ chữ trong `team_engine.text_only_supported(provider)`.
- Khi dựng engine cho lượt chỉ chữ (`team_engine.antigravity_text_only`), hệ thống:
  - Đặt `ac.mode = "suggest"` (kích hoạt sandbox an toàn của CLI).
  - Ngắt file cấu hình MCP riêng (`ac.mcp_config = None`), không nạp các công cụ ngoài từ MCP Hub.
  - Đánh dấu `ac.text_only = True` và `ac.no_tools = True` để đồng bộ hợp đồng kiểm tra kết quả lượt (`TextOnlyOutcome`).
  - Gán định danh nhà cung cấp `self.provider = "antigravity-cli"` trong `server/antigravity_cli.py` và cập nhật bản đồ `_PROVIDER_OF_KIND` trong `server/resonance.py`.

### Lớp 2: Rào chắn bằng chỉ dẫn hệ thống (Prompt Guard)
Do các mô hình ngôn ngữ chạy dưới Antigravity (Gemini 2.0 / 1.5 Pro) có khả năng tuân thủ chỉ dẫn prompt rất cao, hệ thống chèn trực tiếp quy tắc bắt buộc vào `instructions` của phiên gọi:
```text
[QUY TẮC ĐIỀU PHỐI / NHẬN VIỆC THUẦN VĂN BẢN]
- Lượt này là lượt điều phối hoặc nhận việc thuần văn bản trong nhóm (text-only team turn).
- TUYỆT ĐỐI KHÔNG gọi bất kỳ công cụ nào (không gọi read_file, write_file, edit_file, run_command, manage_task...).
- Hãy trả lời trực tiếp bằng văn bản hoặc JSON theo đúng yêu cầu.
```

---

## 3. Các tệp mã nguồn thay đổi

1. `server/team_engine.py`:
   - `text_only_supported(provider)`: Trả về `True` cho `aux_engine.ANTIGRAVITY`.
   - `antigravity_text_only(ac, instructions)`: Hàm cấu hình `AntigravityCLI` sang chế độ text-only kết hợp prompt guard.
   - `build(spec, base, tag, settings)`: Xử lý nhánh `prov == aux_engine.ANTIGRAVITY`, trả về engine đã áp dụng ngoại lệ thay vì ném `TeamEngineUnavailable`.
   - `choose_spec(...)`: Cho phép giữ lại model riêng của trợ lý khi dùng `antigravity-cli` thay vì ép quay về model chính.
2. `server/antigravity_cli.py`:
   - Gán `self.provider = "antigravity-cli"`, `self.text_only = False`, `self.no_tools = False` trong hàm khởi tạo `__init__`.
3. `server/resonance.py`:
   - Bổ sung `"AntigravityCLI": "antigravity-cli"` vào `_PROVIDER_OF_KIND`.
4. `tests/python/test_team_antigravity_coordinator.py`:
   - Bộ kiểm thử tự động xác minh: `text_only_supported`, `choose_spec`, chế độ suggest, cờ `text_only/no_tools` và nội dung prompt guard.

---

## 4. Kết quả nghiệm thu

- Chạy test kiểm thử tự động:
  ```bash
  python3 tests/run.py team_antigravity_coordinator
  # 1/1 xanh trong 0 giây
  ```
- Kiểm thử hồi quy toàn bộ luồng công cụ nhóm:
  ```bash
  python3 tests/run.py team_work_tools
  python3 tests/run.py team_runner
  # Toàn bộ các ca kiểm thử đạt 100%.
  ```
- Các bot điều phối và tự động sửa CI khi dùng model `antigravity-cli` không còn bị vấp lỗi `Không dựng được engine: antigravity-cli chưa chạy được lượt nhóm chỉ chữ`.
