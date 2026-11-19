# Đặc tả kỹ thuật: Hỗ trợ tra cứu GitHub PR và HEAD SHA trong Team Chat

Tài liệu đặc tả kỹ thuật (Spec) viết ngày 19/11/2026.
Vị trí tài liệu: `docs/dev/2026-11-github-team-head-sha-spec.md`.

---

## 1. Bối cảnh & Vấn đề

Trong quy trình phát triển và kiểm thử tự động của nhóm (Team page), các agent (đặc biệt là Kỹ sư DevOps và Người điều phối) cần xác thực mã nguồn của Pull Request (PR) trước khi nghiệm thu:
- Đối chiếu commit HEAD trên GitHub remote của PR để đảm bảo worktree cục bộ đang ở đúng phiên bản mã nguồn cần nghiệm thu.
- Ngăn chặn việc nghiệm thu nhầm trên mã nguồn cũ, ref cục bộ chưa đồng bộ, hoặc dựa vào thông báo không xác thực từ các lượt chat trước.

### Sự cố thực tế phát sinh (Gate Chặng 1)

Lúc kiểm thử xác nhận gate Chặng 1, agent DevOps báo bước kiểm tra bị chặn (`BLOCKED`):
1. **Lệnh git thô qua terminal thoát mã 128 do thiếu xác thực**:
   - Agent chạy lệnh `git ls-remote --exit-code origin refs/pull/1/head` trong terminal sandbox hoặc worktree.
   - Do môi trường terminal sandbox không chứa token GitHub cá nhân của chủ sở hữu (nhằm đảm bảo an toàn bí mật), lệnh git remote thoát lỗi `128` vì từ chối truy cập hoặc không xác thực được remote private.
2. **REST API không xác thực trả lỗi 404**:
   - Khi agent thử dùng curl hoặc script không có header Authorization tới GitHub REST API `GET /repos/owner/repo/pulls/1`, GitHub trả về HTTP `404 Not Found` (hành vi mặc định của GitHub đối với repo riêng tư khi thiếu token).
3. **Công cụ `javis_github` từ chối phục vụ trong Team chat**:
   - Khi agent gọi công cụ chính thống của hệ thống `javis_github(op="status")`, công cụ trả về lỗi:
     ```text
     ERROR: chỉ dùng được trong khung chat web có gắn repo GitHub. Truyền chat_id dạng web:<mã phiên> lấy từ khối KÊNH HỘI THOẠI HIỆN TẠI.
     ```
   - Nguyên nhân: Plugin `javis-github` trước đây chỉ nhận diện các phiên có `chat_id` bắt đầu bằng tiền tố `web:` được truyền tường minh từ chat web. Trong lượt làm việc của nhóm (Team turn), `chat_id` trong ngữ cảnh là mã định danh nhóm (Team ID) hoặc phiên phụ, không mang tiền tố `web:`.
4. **Thiếu thông tin `HEAD SHA` có cấu trúc**:
   - Ngay cả khi `op="status"` chạy được, nó chỉ trả về danh sách PR, trạng thái mergeable chung mà không in rõ mã băm commit `head.sha` để agent đối chiếu với git log cục bộ.

---

## 2. Thiết kế giải pháp

Mục tiêu: Cho phép mọi agent trong nhóm tra cứu được thông tin Pull Request và mã băm `HEAD SHA` chính xác từ GitHub API thông qua kết nối GitHub đã được xác thực sẵn của Javis, hoạt động ở mức quyền đọc an toàn (`readonly`).

### 2.1. Tự động nhận diện phiên trong Team chat (`_sid`)

Tại file `system/plugins/javis-github/plugin.py`, hàm phân giải phiên `_sid(args, ctx)` được nâng cấp theo cơ chế đa tầng:
1. **Nhận diện trực tiếp**:
   - Nếu `chat_id` truyền vào có sẵn tiền tố `web:`, lấy phần mã phiên sau tiền tố.
   - Nếu `chat_id` là chuỗi hex 32 ký tự hợp lệ, chấp nhận trực tiếp làm mã phiên mà không ép buộc phải có tiền tố `web:`.
2. **Nhận diện từ ngữ cảnh lượt (Turn Context & Team Gate)**:
   - Đọc `turn_context.current()` để lấy thông tin phiên đang chạy.
   - Nếu lượt đang giữ cổng repo nhóm (`team_gate`), tra cứu `team_gate.active_gate(gate_id)` để lấy trực tiếp `session_id` của phiên gắn repo.
3. **Nhận diện qua tham số `repo`**:
   - Nếu người gọi truyền tham số `repo="owner/name"` (hoặc ngữ cảnh nhóm có gán repo), plugin tra cứu bảng cơ sở dữ liệu `chat_sessions` trong `ctx.vault_root` để tìm phiên gần nhất đang gắn đúng repo đó.

### 2.2. Bổ sung trường `HEAD: <head_sha>` trong `op="status"`

Trong thao tác `op="status"`:
- Khi tra cứu PR đang mở của phiên (`gh_get_pr`), hệ thống đọc trường `head.get("sha")`.
- Định dạng kết quả trả về văn bản được bổ sung dòng:
  ```text
  HEAD: <head_sha> (nhánh: <head_ref>)
  ```
- Nhờ đó, agent chỉ cần gọi `op="status"` là nhận được ngay SHA của commit mới nhất trên GitHub để đối chiếu với commit HEAD cục bộ.

### 2.3. Bổ sung thao tác chuyên biệt `op="pr"` và `op="head"`

Để phục vụ việc kiểm tra tự động dạng JSON hoặc đọc nhanh:
- **`op="pr"`**: Trả về chuỗi JSON chứa đầy đủ cấu trúc của PR:
  ```json
  {
    "number": 1,
    "title": "...",
    "state": "open",
    "head_sha": "dcfa328b5015ba564a1995f92876d9bfb84b0f91",
    "base_sha": "51952ebd87263e679e49cdc1b2f4c0e5543c854b",
    "mergeable": true,
    "mergeable_state": "clean",
    "html_url": "https://github.com/..."
  }
  ```
- **`op="head"`**: Trả về trực tiếp mã băm `head_sha` hoặc định dạng ngắn gọn:
  ```text
  PR #1 HEAD: dcfa328b5015ba564a1995f92876d9bfb84b0f91 (nhánh: javis/kien-truc-su-phan-mem-chu-6cfe0a)
  ```

### 2.4. Phân quyền an toàn (`readonly`)

Plugin `javis-github` khai báo từ điển phân quyền chi tiết cho từng thao tác:
```python
op_modes = {
    "status": "readonly",
    "pr": "readonly",
    "head": "readonly",
    "diff": "readonly",
    "ci": "readonly",
    "review": "safe",
    "merge": "full",
}
```
- Các thao tác đọc (`status`, `pr`, `head`) thuộc mức `readonly`.
- Mọi agent dù hoạt động ở mức quyền `suggest` (chỉ đọc), `auto` (chỉ ghi file nháp) hay `full` (toàn quyền) đều được phép gọi để kiểm tra và đối chiếu mà không bị cổng phân quyền của MCP Hub chặn lại.

---

## 3. Các tệp mã nguồn thay đổi

1. **`system/plugins/javis-github/plugin.py`**:
   - Nâng cấp `_sid(args, ctx)` tự động nhận diện `chat_id` từ `turn_context`, `team_gate`, hoặc repo.
   - Thêm `HEAD: <head_sha>` trong `op="status"`.
   - Thêm bộ xử lý cho `op="pr"` và `op="head"`.
   - Khai báo `op_modes` với mức `readonly` cho các thao tác tra cứu.

2. **Kiểm thử liên quan**:
   - `tests/python/test_github_merge.py`: Kiểm thử luồng merge an toàn và từ chối ngoài phiên hợp lệ.
   - `tests/python/test_team_git_pr_refs.py`: Kiểm thử đồng bộ ref và đối chiếu SHA commit.

---

## 4. Hướng dẫn sử dụng cho Agent

Khi agent cần kiểm tra commit HEAD của PR đang mở trong nhóm:

```python
# Cách 1: Gọi status chung
javis_github(op="status")
# Kết quả trả về gồm dòng: HEAD: <sha> (nhánh: ...)

# Cách 2: Lấy JSON chi tiết của PR
javis_github(op="pr")

# Cách 3: Lấy trực tiếp HEAD SHA để đối chiếu
javis_github(op="head")
```

Agent đối chiếu SHA nhận được với kết quả của `git rev-parse HEAD` hoặc `git log -1` cục bộ để đảm bảo checkout đúng mã nguồn trước khi thực thi kiểm thử nghiệm thu.
