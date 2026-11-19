# Đặc tả kỹ thuật: Cổng kiểm soát chuyển giao kết quả và báo cáo tự quản trong trang Team

Tài liệu đặc tả kỹ thuật (Spec) viết ngày 19/11/2026.
Vị trí tài liệu: `docs/dev/2026-11-team-autonomous-coordination-gate-spec.md`.

---

## 1. Bối cảnh và Vấn đề

Trong trang Team của Javis OS, người dùng có thể cấp quyền tự quyết cho Người điều phối thông qua 3 mức:
- Mức 1: **Hỏi tôi trước** (Quyền giới hạn).
- Mức 2: **Tự làm, báo tôi** (Toàn quyền với trần vừa phải, không ngắt việc).
- Mức 3: **Tự quản cả team** (Toàn quyền trần mở rộng, `can_interrupt: True`).

### Điểm nghẽn thực tế khi bật "Tự quản cả team"

Khi người dùng giao việc cho nhóm ở mức "Tự quản cả team":
1. Người điều phối mở việc và phân công cho agent cộng sự thực hiện (ví dụ: "Người chạy test E2E").
2. Agent cộng sự thực thi xong việc (chạy công cụ, kiểm thử, sinh mã), in ra kết quả ("Kết quả việc").
3. **Hiện tượng ngắt quãng (Broken Feedback Loop)**:
   - LLM của agent cộng sự thường chỉ tập trung báo cáo kết quả kỹ thuật và bỏ sót việc gọi tên/tag `@Người điều phối`.
   - Về phía lõi `server/team_runner.py`: Hàm `_after_steps()` trước đây quy định nếu việc nhóm chỉ gồm đúng 1 bước công việc (`len(results) == 1`), hệ thống sẽ trực tiếp gọi `_finish()` đóng việc mà bỏ qua bước tổng hợp (`synthesize`) của Người điều phối.
   - Khi việc đóng lại mà không có ai gọi ai, phòng trò chuyện của nhóm dừng hẳn. Người điều phối không nhận được kết quả bàn giao để nghiệm thu hoặc mở tiếp việc tiếp theo, và cũng không báo cáo lại cho Chủ nhóm.
   - Người dùng rơi vào tình thế "chờ đợi trong im lặng" và buộc phải can thiệp thủ công để nhắc người điều phối làm tiếp.

---

## 2. Thiết kế giải pháp: Cổng kiểm soát điều phối 2 chiều

Để đảm bảo tính liên tục và khép kín của chu trình tự vận hành khi đã cấp quyền "Tự quản cả team", giải pháp bổ sung cơ chế kiểm soát có tính tất định ở tầng hệ thống:

```
[Chủ nhóm giao việc ban đầu]
              │
              ▼
    [Người điều phối lập kế hoạch]
              │ (assign)
              ▼
    [Agent cộng sự thực hiện việc]
              │
              ▼
    ┌──────────────────────────────────────────────┐
    │ CỔNG 1: Chuyển giao về Người điều phối       │
    │ (_after_steps: kích hoạt synthesize kể cả 1  │
    │  kết quả khi nhóm ở mức Tự quản cả team)     │
    └──────────────────────────────────────────────┘
              │
              ▼
    [Người điều phối nghiệm thu & tổng hợp]
              │
      ┌───────┴────────────────────────┐
      │                                │
 (Mở việc tiếp theo)          (Không còn việc nào cần giao)
      │                                │
      ▼                                ▼
[Hàng đợi Kanban nhận việc mới]  ┌─────────────────────────────────────────┐
                                 │ CỔNG 2: Báo cáo Chủ nhóm                │
                                 │ (_apply_step_locked / _finish: gắn cờ   │
                                 │  asks_owner=True và gửi notify máy chủ) │
                                 └─────────────────────────────────────────┘
```

### Chi tiết các cổng kiểm soát

#### Cổng 1: Chuyển giao kết quả về Người điều phối (Collaborator -> Coordinator Handoff Gate)
- Bổ sung phương thức `_is_team_autonomous(team)` trong `TeamRunner` để kiểm tra nhóm có đang ở chế độ Toàn quyền Level 3 (`can_interrupt: True`) hay không.
- Trong `_after_steps()`:
  - Nếu `len(results) == 1` nhưng người làm bước đó khác Người điều phối (`results[0]["agent_key"] != team["leader_key"]`) và nhóm đang ở chế độ Tự quản (`_is_team_autonomous`):
  - Hệ thống **không đóng việc trực tiếp** mà tự động thêm bước `synthesize` cho Người điều phối với đầu vào (`inputs`) là mã tin kết quả của cộng sự.
  - Điều này biến việc chuyển giao thành quy tắc hệ thống, không phụ thuộc vào việc LLM của cộng sự có nhớ tag tên Người điều phối hay không.

#### Cổng 2: Kiểm soát báo cáo Chủ nhóm (Coordinator -> Owner Report Gate)
- Trong `_apply_step_locked()` (khi bước `synthesize` hoàn thành) và trong `_finish()`:
  - Hệ thống kiểm tra xem Người điều phối có phát lệnh mở thêm việc mới (`open_job`) hay không, và trong hàng đợi nhóm (`team_queue`) có việc nào khác đang chạy/chờ hay không.
  - **Nếu có việc tiếp theo**: Giữ luồng tự động cho việc mới, không làm phiền Chủ nhóm giữa chừng.
  - **Nếu không còn việc nào cần giao tiếp**: Hệ thống tự động gắn cờ `asks_owner: True` vào siêu dữ liệu tin nhắn (`meta`) và phát thông báo (`_notify`) về thiết bị của Chủ nhóm.
  - Trên giao diện web, tin nhắn được đánh dấu nhãn chờ chủ xem và danh sách nhóm hiện biểu tượng `@` thông báo.

#### Lớp chỉ dẫn bổ sung trong Prompt (Prompt Guidance)
- Cập nhật `WORK_TOOLS_NOTE`: Nhắc nhở cộng sự sau khi hoàn thành bước việc có công cụ phải bàn giao kết quả rõ ràng để Người điều phối nghiệm thu.
- Cập nhật `_body_core()` cho bước `synthesize`:
  - Khi nhóm ở chế độ Tự quản, nhắc nhở Người điều phối: nếu quy trình còn bước sau thì mở việc mới bằng lệnh `open_job`; nếu mọi việc đã hoàn tất thì bắt buộc phải báo cáo lại cho Chủ nhóm (kèm tag `@Chủ nhóm`).

#### Bảo toàn tương thích ngược
- Với các nhóm ở mức **Quyền giới hạn** (Level 1), việc nhóm gồm 1 bước vẫn kết thúc trực tiếp tại tin của cộng sự như cũ, không phát sinh thêm lượt gọi model tổng hợp.

---

## 3. Các tệp mã nguồn thay đổi

1. `server/team_runner.py`:
   - `_is_team_autonomous(team)`: Hàm xác định trạng thái Toàn quyền Level 3 ("Tự quản cả team").
   - `WORK_TOOLS_NOTE`: Bổ sung chỉ dẫn bàn giao kết quả cho người điều phối.
   - `INTERNAL_GROUP_NOTE`: Nhắc nhở thành viên bàn giao kết quả khi hoàn thành công việc.
   - `_body_core()`: Bổ sung chỉ dẫn điều phối tự quản cho bước `synthesize`.
   - `_after_steps()`: Cổng chuyển giao bước `synthesize` khi có 1 kết quả từ cộng sự.
   - `_apply_step_locked()`: Cổng kiểm tra và gắn cờ `asks_owner` khi người điều phối không mở việc tiếp theo.
   - `_finish()`: Đảm bảo tin kết quả việc từ người điều phối báo cáo cho chủ nhóm khi hàng đợi việc của nhóm đã hoàn tất.
2. `tests/python/test_team_autonomous_coordination.py`:
   - Kiểm thử toàn diện 3 kịch bản:
     - Kịch bản 1: Nhóm tự quản, cộng sự làm xong -> bước tổng hợp được kích hoạt -> gắn cờ `asks_owner` và gửi thông báo.
     - Kịch bản 2: Nhóm tự quản, người điều phối mở việc giai đoạn 2 -> việc mới được xếp hàng và không gắn cờ `asks_owner` sớm.
     - Kịch bản 3: Nhóm không tự quản (Level 1) -> giữ nguyên hành vi kết thúc trực tiếp 1 bước cũ.
3. `changelog.d/team-autonomous-coordination-gate.md`:
   - Ghi chú phát hành phiên bản minor.

---

## 4. Kết quả nghiệm thu

- Kiểm thử đơn vị mới:
  ```bash
  python3 tests/run.py test_team_autonomous_coordination
  # 1/1 xanh trong 2 giây (toàn bộ 9/9 assertions pass)
  ```
- Kiểm thử hồi quy hệ thống Team:
  ```bash
  python3 tests/run.py test_team_chat_open_job_0452 test_team_authority test_team_step_deps_0437 test_team_messages
  # 4/4 xanh trong 11 giây
  ```
- Kiểm tra tính hợp lệ của changelog:
  ```bash
  python3 tools/release.py check
  # ok changelog.d/team-autonomous-coordination-gate.md
  ```
