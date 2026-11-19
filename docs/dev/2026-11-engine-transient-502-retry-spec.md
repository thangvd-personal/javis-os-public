# Đặc tả kỹ thuật: Tự động tạm ngưng 30s và thử lại khi LLM gặp lỗi 502 tạm thời

Tài liệu đặc tả kỹ thuật (Spec) viết ngày 19/11/2026.
Vị trí tài liệu: `docs/dev/2026-11-engine-transient-502-retry-spec.md`.

---

## 1. Bối cảnh & Vấn đề

Khi hệ thống Javis OS kết nối tới các nhà cung cấp mô hình ngôn ngữ lớn (LLM) qua cổng API hoặc gateway trung gian (đặc biệt là OpenAI Compatible, Anthropic API, Groq, DeepSeek, v.v.):
- Các máy chủ proxy, reverse proxy (như Cloudflare, Nginx, OneAPI, LiteLLM, vLLM) hoặc chính cụm máy chủ LLM thường xuyên gặp tình trạng quá tải, nghẽn mạng ngắn hạn hoặc timeout tạm thời.
- Khi điều đó xảy ra, gateway upstream trả về mã trạng thái HTTP `502 Bad Gateway` (hoặc `503 Service Unavailable`, `504 Gateway Timeout`, `529 Overloaded`). Ví dụ thực tế:
  ```text
  engine báo lỗi (OpenAI Compatible 502: Vui lòng thử lại hoặc liên hệ Admin. Mã lỗi: 502-5f26e014 (api_error))
  ```

### Hạn chế trước đây

1. **Thiếu cơ chế thử lại cho mã lỗi 502**:
   - Trong `server/engine.py`, hàm `_gui_vong` chỉ thử lại khi xảy ra lỗi socket/network rớt kết nối (`httpx.NetworkError`), không thử lại khi nhận được đối tượng phản hồi HTTP mang mã trạng thái 502.
   - Vòng gọi công cụ `_cc_tool_loop` và vòng stream chữ `_openai_compat_stream` khi nhận `r.status_code != 200` lập tức tạo sự kiện `ev_loi_http` và kết thúc (`return`) ngay ở lần gọi đầu tiên.
2. **Làm hỏng tiến trình và báo lỗi không cần thiết**:
   - Một cú vấp mạng 502 kéo dài vài giây là đủ để giết trọn lượt làm việc của agent (trong nhóm hoặc trong chat riêng).
   - Sự kiện lỗi được đẩy lên `team_runner`, hiển thị thông báo lỗi thô `engine báo lỗi (OpenAI Compatible 502: ...)` vào khung chat chung của nhóm, buộc người dùng hoặc chủ nhóm phải thao tác chạy lại thủ công.
3. **Mất công sức của các vòng gọi công cụ trước đó**:
   - Trong một lượt làm việc phức tạp (ví dụ agent đã thực thi 3-4 lệnh đọc file hoặc kiểm tra git), nếu vòng gọi mô hình tiếp theo bị 502 và fail cả lượt, agent phải chạy lại toàn bộ từ đầu thay vì chỉ cần thử lại đúng request HTTP vừa bị nghẽn.

---

## 2. Thiết kế giải pháp

Mục tiêu: Khi phát hiện phản hồi HTTP mang mã lỗi tạm thời (502, 503, 504, 529 hoặc 5xx có dấu hiệu nghẽn), hệ thống tự động phát thông báo tạm nghẽn, tạm ngưng khoảng 30 giây rồi gửi lại request. Nếu sau lượt thử lại vẫn tiếp tục thất bại, hệ thống mới thông báo lỗi về nhóm chung như trước đây.

### 2.1. Cấu hình tham số nhịp chờ và số lần thử lại

Tại `server/engine.py`, hệ thống định nghĩa các hàm lấy tham số động từ môi trường:
```python
def _transient_502_retries() -> int:
    try:
        return max(0, int(os.getenv("JAVIS_TRANSIENT_502_RETRIES", "1")))
    except (TypeError, ValueError):
        return 1

def _transient_502_wait_s() -> float:
    try:
        return max(0.01, float(os.getenv("JAVIS_TRANSIENT_502_WAIT_S", "30.0")))
    except (TypeError, ValueError):
        return 30.0
```
- **Số lần thử lại (`JAVIS_TRANSIENT_502_RETRIES`)**: Mặc định là 1 lần (tổng cộng tối đa 2 lần gửi request).
- **Thời gian tạm ngưng (`JAVIS_TRANSIENT_502_WAIT_S`)**: Mặc định là 30.0 giây (hoặc ưu tiên giá trị lớn hơn từ header `Retry-After` nếu nhà cung cấp chỉ định).
- **Nâng ngưỡng trần chờ cửa sổ (`_WINDOW_WAIT_MAX = 35.0`)**: Trước đây trần là 25.0s; việc nâng lên 35.0s đảm bảo nhịp chờ 30s không bị hàm điều phối `thu_lai_khi_tam_thoi` hủy ngang vì coi là vượt trần thời gian chờ.

### 2.2. Nhận diện lỗi quá tải (`_qua_tai`)

Hàm `_qua_tai(ev)` được cập nhật để ghi nhận các mã 502 và 504:
```python
def _qua_tai(ev: dict) -> bool:
    """The provider is overloaded (502/503/504/529 or an 'overloaded'/'high demand' body), not a broken request."""
    low = str(ev.get("content") or "").lower()
    return ev.get("ma") in (502, 503, 504, 529) or any(p in low for p in ("overloaded", "high demand", "at capacity"))
```

### 2.3. Vòng thử lại trong vòng gọi công cụ (`_cc_tool_loop` và `anthropic_chat_with_mcp`)

Trong mỗi vòng lặp `_vong` của `_cc_tool_loop`:
1. Kiểm tra trạng thái phản hồi HTTP:
   ```python
   is_transient_502 = (r.status_code in (502, 503, 504, 529)
                       or (r.status_code >= 500 and _is_transient_body(body_text)))
   ```
2. Nếu thỏa mãn và số lần thử lại chưa vượt quá giới hạn:
   - Tăng bộ đếm `transient_502_retries`.
   - Tính thời gian chờ: `cho = max(wait_s, _parse_retry_after(r.headers) or wait_s)`.
   - Phát sự kiện trạng thái:
     ```python
     yield {"type": "tool_call", "name": "javis_wait_retry",
            "content": _c(f"⚙ {label} tạm nghẽn ({r.status_code}), tạm ngưng {cho:.0f}s rồi thử lại...",
                          f"⚙ {label} transient error ({r.status_code}), waiting {cho:.0f}s before retrying...")}
     ```
   - Chờ bất đồng bộ `await asyncio.sleep(cho)`.
   - Tiếp tục vòng lặp (`continue`) để gửi lại request HTTP của chính vòng đó.
3. Khi phản hồi trả về mã `200 OK`: Đặt lại `transient_502_retries = 0` để các vòng công cụ tiếp theo (nếu có) vẫn có đầy đủ quota thử lại khi gặp sự cố mới.
4. Nếu đã thử lại mà vẫn tiếp tục gặp lỗi: Hệ thống thoát ra và phát `yield ev_loi_http(...)` để báo lỗi như bình thường.

### 2.4. Vòng thử lại trong luồng stream chữ trực tiếp (`_openai_compat_stream` và `_anthropic_stream_once`)

Trong luồng stream chữ (không công cụ):
- Bọc khối gửi request trong vòng lặp `while True:`.
- Khi `r.status_code != 200`, nếu là lỗi tạm thời 502/5xx và chưa hết lượt thử lại, ghi log ra `sys.stderr`, tạm ngưng 30s và `continue` để gửi lại request kết nối.
- Vì việc thử lại diễn ra trước khi bất kỳ token nội dung (`text`) nào được yield ra ngoài, việc thử lại hoàn toàn sạch sẽ, không gây hiện tượng lặp lại chữ trên màn hình.

### 2.5. Bảo toàn đếm công cụ trong `ToolTurnOutcome` (`server/team_runner.py`)

Trong `server/team_runner.py`, lớp `ToolTurnOutcome` theo dõi số lượng công cụ mà agent đã thực thi:
- Cơ chế tự động thử lại ở cấp job (`team_retry.py`) có luật nghiêm ngặt: Một bước công việc chỉ được phép tự động chạy lại nếu nó chưa thực hiện công cụ nào (`tool_calls == 0`).
- Nếu sự kiện thông báo `javis_wait_retry` mang `type: "tool_call"` làm tăng `self.tool_calls`, hệ thống sẽ hiểu nhầm là agent đã thực hiện hành động thật (như sửa file, gửi tin nhắn) và từ chối thử lại job nếu lượt đó thất bại.
- Giải pháp: Cập nhật hàm `note(ev)` của `ToolTurnOutcome` để bỏ qua các pseudo tool event nội bộ:
  ```python
  def note(self, ev: Any) -> None:
      if isinstance(ev, dict) and ev.get("type") in ("tool_call", "tool_use"):
          name = str(ev.get("name") or ev.get("tool") or "")
          if not name.startswith("javis_wait_") and name not in ("javis_thinking_off", "javis_no_tools"):
              self.tool_calls += 1
      super().note(ev)
  ```

---

## 3. Các tệp mã nguồn thay đổi

1. **`server/engine.py`**:
   - Thêm `_transient_502_retries()` và `_transient_502_wait_s()`.
   - Nâng `_WINDOW_WAIT_MAX = 35.0`.
   - Cập nhật `_qua_tai()` nhận diện 502/504.
   - Thêm vòng retry 30s cho `_cc_tool_loop`, `_openai_compat_stream`, `anthropic_chat_with_mcp`, `_anthropic_stream_once`.
2. **`server/team_runner.py`**:
   - Cập nhật `ToolTurnOutcome.note()` bỏ qua `javis_wait_*` khỏi đếm `tool_calls`.
3. **`tests/python/test_engine_transient_502_retry.py`**:
   - Bộ kiểm thử tự động với MockTransport kiểm tra đầy đủ các kịch bản thử lại thành công và hết lượt.

---

## 4. Kiểm thử & Xác minh

Chạy bộ kiểm thử tự động:
```bash
./.venv/bin/python tests/python/test_engine_transient_502_retry.py
```
Kết quả kiểm thử:
- `test_cc_tool_loop_retries_502_and_succeeds`: Lần 1 trả 502 -> phát sự kiện `javis_wait_retry` -> tạm ngưng -> lần 2 trả 200 -> thành công, không phát lỗi.
- `test_cc_tool_loop_exhausts_retries_and_yields_error`: Trả 502 cả 2 lần -> thử lại 1 lần rồi phát sự kiện lỗi `OpenAI Compatible 502`.
- `test_openai_compat_stream_retries_502_and_succeeds`: Stream lần 1 lỗi 502 -> lần 2 thành công -> stream đầy đủ nội dung chữ.
- `test_tool_turn_outcome_ignores_pseudo_tool_calls`: `javis_wait_retry` và `javis_wait_quota` không làm tăng bộ đếm `tool_calls`.
- `test_qua_tai_recognizes_502`: Nhận diện chuẩn xác lỗi quá tải cho 502.
