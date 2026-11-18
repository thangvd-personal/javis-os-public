# Tài liệu kỹ thuật: Nhật ký việc làm của Team (Team Audit Log)

Spec viết ngày 18/11/2026. Tài liệu hóa kiến trúc, thiết kế và triển khai tính năng xem, kiểm toán chi tiết và lọc nhật ký làm việc của các cộng sự trong trang Team.

---

## 1. Bối cảnh & Vấn đề

Trong trang Team, các cộng sự (agents) phối hợp thực hiện các công việc phức tạp gồm nhiều bước (lập kế hoạch `plan`, thực thi `work`, tổng hợp `synthesize`, hỗ trợ `help`, trả lời `leader_reply`, xung phong `member_volunteer`).

Trước phiên bản này:
- **Thiếu giao diện xem nhật ký trực quan:** Dù hệ thống có cờ "Ghi nhật ký việc làm" (`audit_enabled`), người dùng không có cách nào xem lại trực tiếp trên giao diện nhóm chat để biết agent đã gọi công cụ gì, gửi prompt gì đến LLM và LLM phản hồi ra sao.
- **Thông báo lỗi chung chung:** Khi một cộng sự gặp lỗi (ví dụ OpenAI Compatible suy nghĩ hết lượt, timeout, context limit), hệ thống hiển thị thông báo "Trợ lý gặp lỗi khi làm...", khiến người dùng không phân biệt được lỗi phát sinh từ thành viên nào trong nhóm.
- **Chưa có khả năng kiểm toán đa chiều:** Không thể lọc lịch sử hoạt động theo thời gian, theo từng cộng sự, theo loại hành động, theo công cụ đã gọi, hoặc theo trạng thái thành công/thất bại/cần phê duyệt.

---

## 2. Mục tiêu thiết kế

1. **Hiển thị định danh cộng sự rõ ràng:** Thông báo lỗi phản ánh chính xác tên cộng sự gặp sự cố (ví dụ: `[Tên cộng sự] gặp lỗi khi làm: ...`).
2. **CTA mở nhật ký thuận tiện:** Bổ sung nút CTA (`.tm-audit-btn`) với biểu tượng `scroll-text` trên thanh tiêu đề của nhóm chat (`.tm-chat-head`).
3. **Khu vực hiển thị nhật ký toàn diện:** Sử dụng toàn bộ diện tích của khung chat (`.tm-chat`) để hiển thị view nhật ký (`.tm-audit-view`), ẩn tạm thời luồng tin nhắn và ô soạn thảo nhưng vẫn giữ thanh tiêu đề nhóm.
4. **Bộ lọc đa chiều và Tìm kiếm từ khóa:**
   - **Khoảng thời gian:** Tất cả, 1 giờ qua, 24 giờ qua, 7 ngày qua, 30 ngày qua.
   - **Cộng sự:** Lọc theo từng thành viên trong nhóm.
   - **Thao tác:** Lọc theo `work`, `plan`, `synthesize`, `help`, `leader_reply`, `member_volunteer`.
   - **Công cụ:** Có gọi công cụ, không gọi công cụ, hoặc lọc theo từng tên công cụ cụ thể (`bash`, `read_file`, `write_file`...).
   - **Trạng thái:** Hoàn thành (`completed`), Lỗi (`error`), Cần duyệt (`approval`).
   - **Tìm kiếm từ khóa:** Tìm kiếm toàn văn trong prompt, response, tên công cụ, câu lệnh, kết quả output, thông báo lỗi, tiêu đề việc.
5. **Chi tiết cuộc gọi LLM & Công cụ:**
   - Xem câu lệnh, đối số và kết quả trả về của từng lượt gọi công cụ.
   - Accordion xem System Prompt, User Prompt gửi đến LLM và toàn bộ nội dung phản hồi text từ LLM.
   - Thống kê lượng token (in/out) và chi phí ước tính ($).
   - Nút bật/tắt ghi nhật ký tức thì, nút tải lại và nút xuất dữ liệu ra file JSON.

---

## 3. Kiến trúc & Luồng dữ liệu

```
[ Team Runner (team_runner.py) ]
       |
       |-- Thực thi lượt làm việc của Agent (work / plan / synth)
       |-- Gọi LLM engine qua API / CLI
       |-- Chạy các công cụ MCP / Native
       |-- Bắt lỗi / Timeout / Xin phê duyệt
       v
[ Team Audit Service (team_audit.py) ]
       |
       |-- Ghi file JSONL: state/team_audit_{team_id}.jsonl
       |-- Ghi SQLite: state/team_audit.sqlite3 (bảng team_audit)
       v
[ Team API (team_api.py) ]
       |
       |-- GET /teams/{team_id}/audit?limit=300
       |     * Làm giàu task_title từ kanban task_store
       |     * Chuẩn hóa trường status và lỗi
       |-- POST /teams/{team_id}/audit/toggle
       v
[ Dashboard UI (dashboard/teams.js & console.css) ]
       |
       |-- CTA .tm-audit-btn trên .tm-chat-head
       |-- View .tm-audit-view trong .tm-chat (.tm-chat.audit-on)
       |-- Thanh bộ lọc đa chiều & tìm kiếm từ khóa
       |-- Danh sách thẻ nhật ký (.tm-audit-card)
       |-- Xuất file JSON (.tm-audit-export)
```

---

## 4. Chi tiết triển khai

### 4.1. Định danh lỗi và Ghi nhận nhật ký (`server/team_runner.py`)
- **Thay đổi thông báo lỗi:** Cập nhật thông điệp báo lỗi từ `"Trợ lý gặp lỗi khi làm: ..."` thành `f"{w} gặp lỗi khi làm: {e}"`, trong đó `{w}` là tên hiển thị của cộng sự thực hiện bước đó.
- **Tự động ghi audit bản ghi lỗi:**
  - Khi agent gặp exception hoặc timeout (ví dụ engine timeout sau 120s), gọi `team_audit.record_audit(...)` với `status="error"` và lưu thông báo lỗi trong `extra_details={"error": str(e)}`.
  - Khi lượt chạy tạo yêu cầu phê duyệt từ chủ (`ask_approval`), ghi nhận `status="approval"`.

### 4.2. Lưu trữ và Truy vấn Audit (`server/team_audit.py`)
- Mở rộng hàm `record_audit` tiếp nhận tham số `status: str = "completed"`.
- Cập nhật hàm `get_audit_entries(team_id, limit=300)`:
  - Cho phép cấu hình `limit` lên tới 1000 bản ghi.
  - Tự động gán fallback `status` dựa trên sự hiện diện của trường `error` hoặc `ask_approval` đối với các bản ghi lịch sử cũ.

### 4.3. API Endpoint (`server/team_api.py`)
- Endpoint `GET /teams/{team_id}/audit`:
  - Tiếp nhận tham số `limit` (mặc định 300).
  - Tra cứu và bổ sung `task_title` từ `task_store.get_task(task_id)` để giao diện hiển thị tên công việc thay vì mã ID kỹ thuật.
- Endpoint `POST /teams/{team_id}/audit/toggle`:
  - Bật/tắt cờ `audit_enabled` của nhóm, đồng bộ tức thời với cấu hình nhóm.

### 4.4. Giao diện người dùng (`dashboard/teams.js` & `dashboard/console.css`)
- **State quản lý audit trong `S`:**
  - `auditOpen`: Boolean bật/tắt hiển thị view audit.
  - `auditEntries`: Mảng các bản ghi nhận từ server.
  - `auditLoading`: Trạng thái đang tải dữ liệu.
  - `auditEnabled`: Trạng thái bật/tắt ghi nhật ký của team hiện tại.
  - `auditKw`, `auditTime`, `auditAgent`, `auditAction`, `auditTool`, `auditStatus`: Các tiêu chí lọc đang được chọn.
- **Cấu trúc DOM:**
  - `.tm-chat-head`: Thêm nút `.tm-audit-btn` kế bên nút tìm kiếm và cài đặt nhóm.
  - `.tm-audit-view`: Thêm container bên trong `.tm-chat`. Khi `S.auditOpen = true`, container được mở và gán class `.audit-on` lên `.tm-chat`.
  - CSS rule trong `console.css` ẩn các thành phần tin nhắn và ô soạn thảo khi `.audit-on` kích hoạt:
    ```css
    .tm-chat.audit-on > :not(.tm-chat-head):not(.tm-audit-view) {
      display: none !important;
    }
    ```
- **Hàm xử lý:**
  - `drawAuditView()`: Vẽ khung view nhật ký đầy đủ (thanh công cụ trên, bộ lọc, danh sách thẻ).
  - `renderAuditListOnly()`: Cập nhật nhanh danh sách thẻ và số lượng kết quả khi người dùng gõ tìm kiếm hoặc đổi dropdown lọc mà không vẽ lại thanh điều khiển, tránh giật lag hoặc mất focus.
  - `filterAuditEntries(entries, opts)`: Logic lọc dữ liệu phía client theo 5 chiều và tìm kiếm từ khóa toàn văn.
  - `auditEntryCardHtml(entry, members)`: Tạo mã HTML cho từng thẻ nhật ký, định dạng thời gian, hiển thị công cụ, prompt, phản hồi và cảnh báo lỗi.
  - `exportAuditJson()`: Tạo và tải về file JSON chứa toàn bộ dữ liệu audit của nhóm.

### 4.5. Đa ngôn ngữ (i18n)
Bổ sung đầy đủ 39 khóa song ngữ trong cả `dashboard/i18n/vi.json` và `dashboard/i18n/en.json`, bao gồm nhãn các bộ lọc, thông báo trạng thái, tooltip và hướng dẫn sử dụng. Tuân thủ nghiêm ngặt quy tắc không chứa ký tự tiếng Việt trong `en.json` và không sử dụng ký tự em dash.

### 4.6. Cấu hình mô hình Agent
Chuyển đổi agent `ecc-docs-lookup` trong file cấu hình [brains/Brain Default/agents/ecc-docs-lookup.md](file:///media/thang-vo/Data/SoftwareDev/javis-os-public/brains/Brain%20Default/agents/ecc-docs-lookup.md) sang mô hình `gpt-6.1-sol` (OpenAI Compatible) nhằm hạn chế lỗi giới hạn context và suy nghĩ vượt ngưỡng.

---

## 5. Kiểm thử & Xác minh

1. **Kiểm thử giao diện (`tests/js/test_team_audit_ui.js`):**
   - Kiểm tra 100% các khóa i18n ở cả tiếng Việt và tiếng Anh.
   - Kiểm tra các quy tắc CSS cho view audit và selector `[hidden]`.
   - Kiểm tra render card audit: tên cộng sự, tiêu đề công việc, badge trạng thái, danh sách công cụ, prompt và response.
   - Kiểm tra logic lọc: lọc theo agent, action, tool, status, time và tìm kiếm từ khóa.
2. **Kiểm thử hồi quy (`tests/js/test_teams_ui.js` & `tests/js/test_team_running_steps_ui.js`):**
   - Đảm bảo toàn bộ quy tắc UI của nhóm chat, thẻ việc, CSS `[hidden]` và cấm ký tự em dash đều vượt qua.
3. **Kiểm thử Backend (`tests/python/test_team_api.py` & `tests/python/test_team_runner.py`):**
   - Xác nhận API `/teams/{team_id}/audit` trả về dữ liệu đúng định dạng và runner ghi nhận đầy đủ khi có lỗi hoặc xin phê duyệt.
