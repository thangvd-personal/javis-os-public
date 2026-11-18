# Tài liệu kỹ thuật: Tự phục hồi khối chỉ dẫn JAVIS_TEAM (Team Directive JSON Repair)

Spec viết ngày 18/11/2026. Tài liệu hóa cơ chế tự phục hồi lỗi cú pháp JSON nhẹ trong các khối chỉ dẫn điều khiển `JAVIS_TEAM` của Team Runner.

---

## 1. Bối cảnh & Vấn đề

Trong hệ thống làm việc nhóm (Team), các cộng sự (agents) trao đổi thông tin tự nhiên kèm theo các chỉ dẫn điều khiển có cấu trúc (directives) gửi tới bộ chạy (host / runner) dưới dạng HTML comment:
```html
<!-- JAVIS_TEAM: {"op": "open_job", "title": "...", "brief": "..."} -->
```
Các op bao gồm:
- `open_job`, `propose_job`: Mở việc mới hoặc đề xuất việc trong lượt trò chuyện.
- `assign`: Trưởng nhóm phân công các bước việc cho các cộng sự trong lượt lập kế hoạch (`plan`).
- `request_help`: Nhờ đồng đội hỗ trợ trong lượt làm việc (`work`) hoặc hỗ trợ (`help`).
- `ask_approval`: Xin chủ nhóm phê duyệt hành động nhạy cảm hoặc kế hoạch.
- `react`, `peer_feedback`: Thả cảm xúc hoặc góp ý cho đồng đội.

### Sự cố thực tế phát sinh (Turn `tu_b5b8f60c5958cb28`)
Ở tin nhắn #261 của nhóm `ASCTax PoC`, khi Trưởng nhóm phản hồi yêu cầu bổ sung mockup từ chủ nhóm, model (`gpt-6.1-sol`) đã sinh ra khối lệnh:
```text
<!-- JAVIS_TEAM: {"op": "open_job", "title": "Bổ sung mockup...", "brief": "..."} } -->
```
Do có **thừa một dấu ngoặc nhọn đóng `}` ở cuối (`"} }"` thay vì `"}"`), hàm `json.loads` chuẩn ném lỗi `JSONDecodeError: Extra data`. 

Trước bản vá này:
- Hàm `parse_directives` chỉ có một khối `try ... json.loads(raw) except (json.JSONDecodeError, ...)` duy nhất.
- Khi gặp lỗi, hệ thống bỏ qua toàn bộ khối JSON và ghi nhận cảnh báo: *"Khối JAVIS_TEAM hỏng (không đọc được JSON), đã bỏ"*.
- Hậu quả: Lệnh `open_job` bị mất hoàn toàn, hoặc trong lượt `plan` có thể khiến kế hoạch bị chốt việc sớm mà không phân công cho ai.

---

## 2. Mục tiêu thiết kế

1. **Khả năng tự phục hồi cú pháp nhẹ (Graceful JSON Repair):** Tự động phát hiện và sửa các sai sót cú pháp phổ biến của LLM mà không làm thay đổi ý nghĩa ngữ nghĩa của khối lệnh.
2. **Không thêm dependency bên ngoài:** Sử dụng thư viện chuẩn của Python (`json`, `re`, `ast`), tránh phụ thuộc vào thư viện bên ngoài hoặc các thư viện parser quá khắt khe (`orjson`, `ujson`).
3. **Bảo tồn tính an toàn và tương thích ngược:** Nếu chuỗi JSON bị hỏng nặng không thể phục hồi (chuỗi rác, cắt cụt vô nghĩa), hệ thống vẫn giữ nguyên cơ chế an toàn: loại bỏ khối lệnh và cảnh báo như trước, không gây crash luồng làm việc.

---

## 3. Kiến trúc Pipeline tự phục hồi (`_repair_and_parse_json`)

Hàm `_repair_and_parse_json` trong `server/team_runner.py` hoạt động theo quy trình đa tầng:

```
[ Chuỗi raw từ khối <!-- JAVIS_TEAM: ... --> ]
       |
       v
[ Tầng 1: json.loads(raw) ] ---------------------------> Thành công -> return dict
       | (Thất bại)
       v
[ Tầng 2: json.loads(raw, strict=False) ] --------------> Thành công -> return dict
       | (Cho phép newline \n, ký tự điều khiển chưa escape)
       v
[ Tầng 3: Gọt Markdown fence ```json ... ``` ] ---------> Thành công -> return dict
       | (Thất bại)
       v
[ Tầng 4: raw_decode bỏ ký tự/ngoặc thừa ở đuôi ] -----> Thành công -> return dict
       | (Giải quyết lỗi thừa ngoặc đóng "} }")
       v
[ Tầng 5: Regex loại bỏ dấu phẩy thừa (trailing commas) ] -> Thành công -> return dict
       | (Trước ] hoặc })
       v
[ Tầng 6: ast.literal_eval (Nháy đơn kiểu Python dict) ] -> Thành công -> return dict
       | (Thất bại)
       v
[ Tầng 7: Bỏ qua an toàn ] -----------------------------> return None (ghi note cảnh báo)
```

---

## 4. Chi tiết triển khai

### 4.1. Mã nguồn phục hồi (`server/team_runner.py`)
- Nhập module `ast` ở phần đầu file.
- Định nghĩa hàm `_repair_and_parse_json(raw: str) -> dict | None`:
  1. Thử `json.loads(raw)`.
  2. Thử `json.loads(raw, strict=False)`.
  3. Gọt markdown block nếu có (`re.sub(r"^```(?:json)?\s*", "", ...)`).
  4. Dùng `json.JSONDecoder(strict=False).raw_decode(cleaned)` để lấy object hợp lệ đầu tiên, kiểm tra phần đuôi `remainder` chỉ chứa khoảng trắng hoặc ngoặc đóng/phẩy thừa (`re.fullmatch(r"[\s,;}\]]*", remainder)`).
  5. Khắc phục dấu phẩy thừa trước `}` hoặc `]` bằng `re.sub(r",\s*([\]}])", r"\1", cleaned)` và thử lại cả `loads` lẫn `raw_decode`.
  6. Thử `ast.literal_eval()` an toàn cho dạng dict dùng nháy đơn `'`.
- Cập nhật hàm `parse_directives`: Thay thế khối `try ... json.loads` đơn lẻ bằng lời gọi `_repair_and_parse_json(raw)`.

### 4.2. Kiểm thử tự động (`tests/python/test_team_runner.py`)
Bổ sung ca kiểm thử tự động kiểm tra hàm `TR.parse_directives`:
- Ca kiểm thử chứa khối `open_job` có ký tự xuống dòng chưa escape và thừa dấu ngoặc nhọn đóng `} }`.
- Ca kiểm thử chứa khối `assign` dùng nháy đơn `'` và có dấu phẩy thừa ở cuối dict.
- Xác nhận cả 2 lệnh đều được trích xuất chính xác thành dict, văn bản sạch được bóc tách hoàn chỉnh, và mảng ghi chú `notes` rỗng (không bị đánh dấu lỗi).
- Kiểm tra các ca hỏng hoàn toàn (như `{hỏng`) vẫn được ghi nhận lỗi chính xác.

---

## 5. Kết luận & Khuyến nghị

Bản vá giúp triệt tiêu hoàn toàn các lỗi drop chỉ dẫn do sai lệch cú pháp nhẹ thường gặp ở các LLM, giúp luồng phối hợp công việc của Team chạy liên tục, ổn định và tự động hóa cao hơn.
