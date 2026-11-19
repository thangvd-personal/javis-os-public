# Nâng cấp toàn diện: Hỗ trợ đọc và thêm tệp từ Worktree và Main-tree trong trang Team

Tài liệu đặc tả kỹ thuật (Spec) viết ngày 19/11/2026.
Vị trí tài liệu: `docs/dev/2026-11-team-worktree-maintree-files-spec.md`.

---

## 1. Bối cảnh & Vấn đề

Trong quy trình làm việc của nhóm (Team page) kết nối với kho mã nguồn GitHub:
- Nhóm được cấp hai cây thư mục mã nguồn:
  1. **Worktree** (thư mục làm việc): Nhánh riêng dạng `javis/<session_id>` hoặc theo nhánh tác vụ, là nơi các cộng sự trực tiếp viết mã, kiểm thử và tạo báo cáo nghiệm thu.
  2. **Main-tree** (thư mục chính): Bản clone từ nhánh gốc (nhánh mặc định `main`), dùng để tra cứu mã nguồn đối chiếu.
- Ngoài ra nhóm còn có cây thư mục **Brain** (bộ não cá nhân) lưu trữ các ghi chú, kế hoạch tổng thể.

### Sự cố thực tế phát sinh (Nghiệm thu Chặng 1)
Khi người dùng hoặc agent điều phối tạo báo cáo nghiệm thu trong worktree (ví dụ: `/media/thang-vo/Data/SoftwareDev/javis-os-public/server/state/worktrees/gh-f7acf208636d-6cfe0a99/docs/reports/artifact_nghiem_thu_chang_1.md`), các vấn đề sau xảy ra:

1. **Không thể thêm tệp worktree / main-tree vào danh mục "File của nhóm"**:
   - Khi tìm kiếm tệp hoặc chọn từ cây thư mục để thêm vào mục File (tài liệu đính kèm nhóm), hệ thống báo lỗi không thành công hoặc chỉ chấp nhận tệp nằm trong Brain.
2. **Agent không đọc được nội dung tệp khi người dùng gửi đường dẫn trong tin nhắn**:
   - Trong các lượt hội thoại thông thường (text-only turn không gọi MCP tool), agent chỉ có thể tiếp cận các tài liệu được nạp sẵn vào prompt thông qua khối `_team_docs_block`.
   - Khi người dùng gửi đường dẫn file worktree hoặc main-tree trong tin nhắn (ví dụ: `@Người điều phối agent ... /docs/reports/artifact_nghiem_thu_chang_1.md`), agent chỉ nhìn thấy chuỗi ký tự đường dẫn mà không có nội dung tệp. Hậu quả là agent phải trả lời: *"em đã nhận đường dẫn báo cáo nghiệm thu, nhưng lượt chat này chưa đọc được nội dung file. Anh gắn file vào tài liệu nhóm hoặc dán phần SHA..."*, làm tắc nghẽn quy trình nghiệm thu tự động.
3. **Cây thư mục (tab Thư mục) thiếu thao tác nhanh**:
   - Người dùng bấm chuột phải vào một tệp/thư mục trong cây Worktree hoặc Main-tree nhưng menu ngữ cảnh chỉ có "Gắn vào chat", không có tùy chọn "Thêm vào File nhóm".

---

## 2. Nguyên nhân kỹ thuật

1. **Hạn chế trong mô-đun lưu trữ tài liệu nhóm (`server/team_docs.py`)**:
   - Hàm `save()` kiểm tra nguồn gốc của tệp thông qua logic:
     ```python
     if not (_is_inside(src_p, staging) or _is_inside(src_p, brain_root)):
         continue
     ```
   - Các tệp nằm trong thư mục `worktree` hoặc `main-tree` bị loại bỏ hoàn toàn vì không thuộc `staging` hay `brain_root`.
2. **Hạn chế tại endpoint đính kèm tệp (`_team_attach` trong `server/main.py`)**:
   - Khi nhận tham số đính kèm, hàm giải quyết đường dẫn thông qua `_safe_path(brain_root, raw_p)`. Hàm này ném ngoại lệ `ValueError` nếu đường dẫn nằm ngoài `brain_root`.
   - Hàm chuẩn hóa đường dẫn tương đối `_files_rel(root, p)` gọi `p.relative_to(root)` khiến việc thêm tệp ngoài `root` sinh ngoại lệ `ValueError: '...' is not in the subpath of '...'`.
3. **Hạn chế tại endpoint xem nội dung thô (`GET /files/raw`)**:
   - `_safe_serve_path(brain_root, rel)` chỉ phân giải đường dẫn khi có truyền `team_id` và các thư mục repo của nhóm được định vị chính xác. Nếu không có `team_id`, endpoint từ chối phục vụ các tệp ngoài brain.
4. **Cơ chế nạp tài liệu vào prompt của Agent (`_team_docs_block` và `team_runner.py`)**:
   - Prompt của các thành viên trong nhóm nhận nội dung tài liệu từ `_team_docs_block(team, root, tool_turn)`.
   - Hàm này trước đây chỉ nạp các file đã được ghim sẵn trong `assets.json` của nhóm, không có cơ chế tự động nhận diện và đọc các tệp được người dùng trích dẫn (mention) trực tiếp trong nội dung câu hỏi/yêu cầu.

---

## 3. Thiết kế giải pháp toàn diện

Kiến trúc giải pháp được thiết kế đảm bảo 3 tiêu chí:
1. **Linh hoạt & Tự động**: Người dùng có thể gắn file thủ công qua UI, hoặc chỉ cần dán đường dẫn file trong tin nhắn là agent tự động đọc được nội dung.
2. **An ninh chặt chẽ**: Chỉ cho phép truy cập các tệp nằm trong `brain_root`, `worktrees` và `main_trees` hợp lệ của phiên nhóm. Tuyệt đối chặn các tệp hệ thống (`/etc/passwd`, `/root`, v.v.).
3. **Tương thích ngược**: Không làm thay đổi cấu trúc dữ liệu cũ của `team_docs` và duy trì tính toàn vẹn của các bài test hiện có.

```
+-----------------------------------------------------------------------------------+
|                                  TRANG TEAM                                       |
|                                                                                   |
|  [Tab Thư mục]                                      [Khu vực Chat]                |
|  - Worktree (R/W)                                   - User dán đường dẫn tệp      |
|  - Main-tree (Readonly)                               (hoặc bấm Thêm vào File)     |
|  - BrainRoot (R/W)                                                                |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                              BACKEND / SERVER                                     |
|                                                                                   |
|  1. POST /teams/{tid}/files (_team_attach)                                        |
|     - Xác thực đường dẫn qua _is_allowed_extra_path (worktree / maintree)         |
|     - team_docs.save(extra_roots=..., is_allowed=...)                             |
|                                                                                   |
|  2. Tự động đọc file nhắc đến (_team_mentioned_files_block)                        |
|     - Quét regex các đường dẫn /media/... hoặc path tương đối trong message body   |
|     - Tự động nạp nội dung file vào prompt của agent                              |
|                                                                                   |
|  3. GET /files/raw & GET /files/read                                              |
|     - Nhận diện team_id -> _safe_serve_path phục vụ file worktree / maintree      |
+-----------------------------------------------------------------------------------+
```

---

## 4. Chi tiết triển khai

### 4.1. Mở rộng mô-đun tài liệu nhóm (`server/team_docs.py`)
- Mở rộng tham số hàm `save(dest_dir, attachments, staging_dir, brain_root, extra_roots=None, is_allowed=None)`:
  - `extra_roots`: Danh sách các thư mục gốc hợp lệ bổ sung (worktrees, main_trees).
  - `is_allowed`: Hàm callback kiểm tra quyền truy cập đường dẫn tệp tùy biến.
- Giữ nguyên thuộc tính `source_path` đối với các tệp ngoài brain, giúp các hàm đọc nội dung và kiểm tra tồn tại (`exists`) truy xuất chính xác tệp nguồn trên đĩa.

### 4.2. Xử lý đường dẫn và bảo mật (`server/main.py`)
- Bổ sung hàm kiểm tra an ninh `_is_allowed_extra_path(p, brain_root, team_id=None)`:
  - Thu thập danh sách `worktrees` và `main_trees` của phiên thông qua `_get_team_trees_for_serve(brain_root, team_id)`.
  - Chỉ cho phép tệp nếu đường dẫn nằm trong một trong các thư mục worktree hoặc main-tree đã xác thực.
- Cập nhật `_team_attach(team, root, att)`:
  - Hỗ trợ giải quyết đường dẫn tương đối lẫn tuyệt đối từ `path`, `source_path` hoặc `brain_path`.
  - Khi tệp thuộc worktree hoặc main-tree: Tính toán đường dẫn hiển thị ngắn gọn (`rel_disp`), ghi nhận loại cây thư mục (`tree_kind`) và bảo lưu đường dẫn tuyệt đối an toàn.
  - Từ chối ngay lập tức với mã lỗi 400 nếu đường dẫn cố gắng trỏ tới các tệp hệ thống trái phép.
- Cập nhật các hàm hiển thị tài liệu:
  - `_liet_ke_tai_lieu`: Đọc trực tiếp từ `source_path` nếu tệp nằm ngoài brain.
  - `_folder_asset_text`: Duyệt cây thư mục hợp lệ cho cả thư mục thuộc worktree.
  - `_cuoc_tai_lieu_tay`: Đọc nội dung tệp từ worktree/main-tree để đưa vào ngữ cảnh của agent.
- Thêm cơ chế tự động đọc tệp được nhắc tới trong tin nhắn:
  - `_team_mentioned_files_block(team, root, message_text)`:
    - Tìm kiếm các đường dẫn tuyệt đối hoặc tương đối xuất hiện trong văn bản tin nhắn của người dùng.
    - Phân giải đường dẫn dựa trên các thư mục worktree và main-tree của nhóm.
    - Đọc và định dạng nội dung tệp vào một khối tài liệu tham chiếu:
      ```markdown
      ### Nội dung các tệp được nhắc đến trong tin nhắn:
      #### [Tên file] (worktree: docs/reports/artifact_nghiem_thu_chang_1.md)
      ```
  - Cập nhật `_team_docs_block` nhận thêm tham số `extra_text` để kích hoạt khối tự động này.
- Cập nhật `_safe_serve_path` và `GET /files/raw`:
  - Tiếp nhận `team_id` từ query parameter.
  - Cho phép người dùng xem trực tiếp nội dung các tệp worktree/main-tree qua URL `/files/raw`.

### 4.3. Cầu nối Runner và Agent Prompt (`server/team_runner.py`)
- Cập nhật hàm `_prepare()` trong `TeamRunner`:
  - Lưu giữ nội dung lượt chat hiện tại vào `self._turn_body`.
  - Trong hàm `_docs(team, tool_turn)`: Truyền nội dung lượt chat sang `_team_docs_block(team, self._root, tool_turn, extra_text=self._turn_body)`.
- Nhờ vậy, ngay cả trong các lượt hội thoại không sử dụng tool (văn bản thuần), agent luôn có sẵn toàn văn nội dung của các tệp mà người dùng yêu cầu đối chiếu.

### 4.4. Giao diện người dùng Dashboard (`dashboard/teams.js`, `vi.json`, `en.json`)
- Cây thư mục (tab Thư mục):
  - Thêm mục menu ngữ cảnh: **"Thêm vào File nhóm"** (`treeAddToTeamFiles`).
  - Hàm `treeAddToTeamFiles(it)`: Tự động gọi API `POST /teams/{tid}/files` với thông tin đường dẫn của tệp hoặc thư mục đang chọn trên cây Worktree/Main-tree.
  - Cập nhật hàm `rawUrl(path)`: Tự động gắn kèm `&team_id=...` khi người dùng đang ở ngữ cảnh nhóm để xem tệp thô trực tiếp.
  - Cập nhật `treeAttachChat(it)`: Gắn đúng định dạng tệp để gửi vào khung chat.
- Đa ngôn ngữ (`dashboard/i18n/vi.json` và `en.json`):
  - Cập nhật gợi ý tìm kiếm: *"Tìm file hoặc thư mục trong brain, worktree hoặc main-tree (gõ tên)"*.

---

## 5. Danh mục kiểm thử & Xác nhận chất lượng

Bộ kiểm thử tự động toàn diện được thiết lập tại `tests/python/test_team_worktree_maintree_files.py` bao gồm 10 tiêu chí kiểm tra:

1. **Từ chối truy cập tệp ngoài phạm vi**:
   - Thử thêm `/etc/passwd` vào File của nhóm -> Hệ thống trả về mã lỗi 400 kèm thông báo bảo mật.
2. **Thêm tệp từ Worktree**:
   - Thêm tệp `artifact_nghiem_thu_chang_1.md` từ worktree vào danh sách File nhóm -> Thành công (mã 200).
3. **Thêm tệp từ Main-tree**:
   - Thêm tệp `README.md` từ main-tree vào danh sách File nhóm -> Thành công (mã 200).
4. **Thêm thư mục từ Worktree**:
   - Thêm thư mục `docs/reports` từ worktree -> Hệ thống ghim cây thư mục thành công.
5. **Kiểm tra trạng thái tồn tại (`GET /sessions/{sid}/assets`)**:
   - Tệp worktree, tệp main-tree và thư mục worktree đều có `exists=True`.
6. **Kiểm tra xem trực tiếp (`GET /files/raw`)**:
   - URL phục vụ nội dung tệp worktree và main-tree chính xác với `Content-Type: text/plain`.
7. **Nạp nội dung vào khối tài liệu nhóm (`_team_docs_block`)**:
   - Khối tài liệu nhóm chứa toàn văn nội dung tệp worktree, main-tree và sơ đồ cây thư mục.
8. **Tự động đọc tệp khi nhắc trong tin nhắn**:
   - Khi tin nhắn chat chứa đường dẫn tuyệt đối hoặc tương đối tới tệp trong worktree, `_team_docs_block` tự động nạp nội dung tệp mà không cần người dùng thao tác ghim thủ công.
9. **Gửi tin nhắn kèm tệp đính kèm**:
   - Gửi tin nhắn có đính kèm tệp worktree thành công, tin nhắn lưu vết đầy đủ tên tệp đính kèm.
10. **Kiểm thử hồi quy**:
    - `tests/python/test_team_docs.py`: 100% Passed.
    - `tests/python/test_team_worktree_gate.py`: 100% Passed.
    - `tests/js/test_team_create_docs_ui.js`: 100% Passed.

---

## 6. Kết luận

Bản nâng cấp giải quyết triệt để sự cố nghiệm thu chặng 1, tạo điều kiện cho các agent trong nhóm truy cập liền mạch, bảo mật vào cả ba vùng lưu trữ (Brain, Worktree, Main-tree), đồng thời hỗ trợ người dùng thao tác trực quan từ cây thư mục.
