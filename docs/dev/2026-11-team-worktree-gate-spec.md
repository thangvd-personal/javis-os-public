# Mở rộng phạm vi quyền hạn của Team: Toàn quyền Worktree và Chỉ đọc Main-tree

Spec viết ngày 18/11/2026. Giải quyết yêu cầu mở rộng phạm vi thao tác cho các cộng sự (agents) trong trang Team khi kết nối với GitHub repo dưới thiết lập "Hành động ra ngoài của nhóm" -> "Cần chủ duyệt (khuyên dùng)".

## 1. Bối cảnh & Vấn đề

Trong trang Team, chủ nhóm có thể thiết lập mức quyền hoạt động cho cả nhóm thông qua mục **"Hành động ra ngoài của nhóm"**:
- **Cần chủ duyệt (khuyên dùng)** (`work_mode: gated`, mặc định): các cộng sự được tự do thao tác trong phạm vi được giao, còn các hành động ra ngoài (gửi tin, thanh toán, lệnh máy, xóa dữ liệu...) bị cổng chặn lại trước khi chạy và tạo thẻ xin duyệt.
- **Nhóm tự làm, không hỏi** (`work_mode: full`): toàn quyền như chat riêng.

Khi một nhóm kết nối làm việc với repo GitHub, Javis quản lý hai cây thư mục:
1. **Thư mục làm việc (worktree)**: nhánh riêng `javis/<session_id>` tách từ nhánh gốc (`main`), là nơi các cộng sự trực tiếp đọc, viết mã, chạy kiểm thử và chuẩn bị công việc.
2. **Thư mục chính (main-tree)**: bản clone gốc của repository (chứa `origin/main` hoặc nhánh mặc định), dùng để fetch, pull và làm căn cứ đối chiếu.

### Điểm nghẽn trước đây
Trước phiên bản này, khi nhóm ở mức "Cần chủ duyệt":
- `server/team_gate.py` chỉ nhận danh sách gốc ghi `roots` là `[cwd or vault, vault]`. Nếu `cwd` rỗng hoặc phiên có nhiều repo, các worktree khác không được nhận diện.
- `team_gate` không có khái niệm `readonly_roots`, nên không thể phân biệt giữa thư mục được phép ghi và thư mục chỉ được phép đọc.
- Trong `server/mcp_hub.py`, các tool thao tác file nội bộ (`javis_read_file`, `javis_write_file`, `javis_edit_file`, `javis_list_dir`, `javis_search_files`) không nhận `workspace_root` từ lượt của Team. Do đó, hàm `_safe_path` và `_safe_read_path` chỉ chấp nhận các đường dẫn bên trong `vault` (`ValueError: nằm ngoài vault`). Các cộng sự hoàn toàn không thể chạm vào codebase của repo dù repo đã được gắn vào phiên chat của nhóm.
- Các engine API (`_ApiAuxEngine`) khi chạy bước việc nhóm cũng không được truyền ngữ cảnh thư mục làm việc.

## 2. Thiết kế giải pháp

Mô hình phân vùng quyền hạn trong bước việc nhóm (`HUB_MODE = "gated"`):

| Vùng thư mục | Quyền hạn của Agent | Mục đích & Rào chắn |
|---|---|---|
| **Bộ não (Vault)** | Đọc & Ghi (`free`) | Lưu ghi chú, báo cáo, tài liệu bàn giao. Vẫn áp dụng rào `protected_rel` (chặn ghi đè `plugins/`, `Javis/loops`, `.git`, cấu hình engine). |
| **Thư mục làm việc (Worktree)** | Đọc & Ghi, toàn quyền (`free`) | Nơi agent trực tiếp đọc code, sửa file, tạo file mới, refactor codebase. Rào `protected_rel` bảo vệ metadata `.git`. |
| **Thư mục chính (Main-tree)** | Chỉ đọc (`readonly_roots`) | Cho phép agent đọc code gốc, xem diff, đối chiếu với origin/main. Cấm tuyệt đối thao tác ghi/sửa file để giữ sạch nhánh chính. |
| **Ngoài 3 vùng trên** | Ra ngoài phạm vi (`outside`) | Chặn lại trước khi chạy và tạo thẻ xin duyệt gửi chủ nhóm. |

## 3. Chi tiết triển khai

### 3.1. Cổng kiểm soát công cụ nhóm (`server/team_gate.py`)
- Cấu trúc `Gate`:
  - Thêm thuộc tính `readonly_roots: list` vào `Gate.__slots__` và hàm khởi tạo.
  - Hàm `set_roots(gate_id: str, roots: list, readonly_roots: list | None = None)` chuẩn hóa đường dẫn tuyệt đối cho cả hai danh sách: `g.roots` (thư mục được ghi) và `g.readonly_roots` (thư mục chỉ đọc).
  - Cung cấp hai hàm truy vấn `get_roots()` và `get_readonly_roots()`.
- Phân loại công cụ native (`classify_native` & `native_check`):
  - Nhóm đọc (`NATIVE_FREE` gồm `Read`, `Glob`, `Grep`, `LS`, `NotebookRead`...): Cho phép chạy tự do (`free`) ở cả `roots` và `readonly_roots`.
  - Nhóm ghi (`NATIVE_WRITE` gồm `Write`, `Edit`, `MultiEdit`, `NotebookEdit`):
    - Nếu đường dẫn nằm trong `readonly_roots` (main-tree): Trả về `"outside"` (bị chặn lại, không được phép ghi tự do).
    - Nếu đường dẫn nằm trong `roots` (worktree hoặc vault) và không chạm file được bảo vệ: Trả về `"free"` (thực thi ngay).
    - Các trường hợp còn lại: Trả về `"outside"`.
  - Hàm `_inside`: Hỗ trợ chuẩn hóa cả đường dẫn tương đối lẫn tuyệt đối so với danh sách các gốc.

### 3.2. Mở rộng công cụ file trong MCP Hub (`server/mcp_hub.py`)
- `_safe_path(vault_root, p, workspace_root=None, readonly_workspace_root=None)`:
  - Khi tìm file có sẵn: Ưu tiên theo thứ tự `vault` -> `workspace_root` (worktree) -> `readonly_workspace_root` (main-tree).
  - Khi tạo file mới: Chỉ xét `workspace_root` và `vault`. Không bao giờ tạo file mới vào `readonly_workspace_root`.
- `_safe_read_path`: Cho phép đọc file từ cả `vault`, `workspace_root` và `readonly_workspace_root`.
- `_kiem_ghi` (dùng chung cho `javis_write_file` và `javis_edit_file`):
  - Bổ sung bước kiểm tra rào cứng: Nếu đường dẫn cần ghi rơi vào `readonly_workspace_root`, lập tức trả về lỗi:
    `ERROR: Thư mục chính (main-tree) là chỉ đọc (read-only), không được phép ghi hoặc sửa file tại '...'. Hãy thực hiện các thao tác ghi/sửa trong thư mục làm việc (worktree) hoặc vault.`
  - Trong chế độ `mode == team_gate.HUB_MODE`: Chỉ coi các file thuộc `vault` hoặc `workspace_root` (worktree) là trong phạm vi hợp lệ (`rel is not None`). Các thao tác ghi ngoài phạm vi này đều đi qua `team_gate.hub_check`.
- `_ls` (`javis_list_dir`) và `_search` (`javis_search_files`): Truyền `readonly_workspace_root` để agent có thể liệt kê và tìm kiếm chuỗi trong cả worktree và main-tree.
- `discover_all`:
  - Trong chế độ `mode == team_gate.HUB_MODE`: Tự động trích xuất `workspace_root` từ `g.roots` và `readonly_workspace_root` từ `g.readonly_roots` thông qua `team_gate.current()`.
  - Nhờ vậy, mọi engine (Claude Code, Codex kết nối qua HTTP `/hub/mcp`, hoặc engine API in-process) đều tự động nhận được danh sách thư mục hợp lệ mà không cần cấu hình thủ công từng request.

### 3.3. Dựng engine nhóm và gắn kết Repo (`server/main.py` & `server/aux_engine.py`)
- Thêm hàm trợ giúp `_team_repo_trees(team, root)` trong `server/main.py`:
  - Đọc danh sách repo được gắn với phiên của nhóm qua `github_repos.session_repos(sid, root)`.
  - Tự động gọi `github_repos.ensure_worktree` nếu worktree của repo chưa được tạo trên đĩa.
  - Thu thập danh sách `worktrees` và danh sách `main_trees` (thư mục clone gốc).
  - Bổ sung các thư mục lập trình khác từ `coding_store.thu_muc_cua_phien(sid)` nếu có.
- Trong `_team_engine_for` -> `_build_tools`:
  - Gán `base.workspace_root = worktrees` và `base.readonly_workspace_root = main_trees`.
  - Đăng ký vào cổng duyệt: `team_gate.set_roots(gate_id, write_roots, readonly_roots=main_trees)`, trong đó `write_roots` bao gồm worktrees và vault.
- Trong `server/aux_engine.py`:
  - `_ApiAuxEngine` nhận thêm `workspace_root` và `readonly_workspace_root` từ engine base và truyền vào `mcp_hub.discover_all`.

### 3.4. Cập nhật Prompt và Tài liệu người dùng
- `server/team_runner.py`: Cập nhật `GATED_NOTE` giải thích rõ cho agent về quyền hạn trong worktree (toàn quyền đọc ghi) và main-tree (chỉ đọc).
- `docs/07-agents-va-workflows.md`: Cập nhật tài liệu hướng dẫn về chế độ "Cần chủ duyệt" của Team.
- `dashboard/i18n/vi.json` và `en.json`: Cập nhật tooltip mô tả chế độ `teams.wm.gated_desc`.

### 3.5. Bổ sung ngữ cảnh Worktree & Main-tree khi xem và tìm kiếm file trên Dashboard
Khi agent trả lời trong cuộc hội thoại nhóm và dẫn link tới file (ví dụ `docs/11-review-kien-truc-ban-1.md` hoặc `[docs/11-review-kien-truc-ban-1.md](docs/11-review-kien-truc-ban-1.md)`), trước đây client và server mặc định chỉ tìm trong vault/brain khiến người dùng gặp lỗi "Không tìm thấy file" ("Tìm cả brain cũng không có file nào tên gần giống").

Đã xử lý đồng bộ từ client đến server:
- **Server (`server/main.py`)**:
  - `_get_team_trees_for_serve(brain, team_id=None)`: Trích xuất danh sách `worktrees` và `main_trees` từ phiên làm việc của team (kết hợp `team_gate.current()`, `team_api`, `team_store`, `github_repos` và `coding_store`).
  - `_safe_serve_path(brain, rel, team_id=None)`: Mở rộng khả năng resolve đường dẫn tương đối. Khi không tìm thấy trong trần duyệt hay gốc brain, tự động đối chiếu trong `worktrees` (ưu tiên hàng đầu) và `main_trees` (chỉ đọc). Đồng thời tự động bóc tiền tố vault nếu client truyền kèm.
  - `/files/read`: Tiếp nhận tham số `team_id`. Khi file được đọc từ `main-tree`, tự động đánh dấu `readonly: True`, `editable: False`, `tree_kind: "clone"`. Khi đọc từ `worktree`, đánh dấu `editable: True`, `tree_kind: "worktree"`.
  - `/files/raw`: Tiếp nhận tham số `team_id` và chuyển tiếp cho `_safe_serve_path`.
  - `/files/write`: Ngăn chặn tuyệt đối việc ghi/sửa đè vào `main-tree` (trả về mã 403 nếu đường dẫn rơi vào `main_trees`).
  - `/files/search`: Mở rộng tìm kiếm ngoài gốc brain sang cả các thư mục `worktrees` và `main_trees` của nhóm. Trả về thông tin `tree_kind` ("worktree" hoặc "clone") và đường dẫn tương đối `rel_path` để hiển thị trực quan.
- **Client (`dashboard/teams.js` & `dashboard/console.js`)**:
  - `dashboard/teams.js`:
    - `indexTreeItems`: Index các node cây thư mục theo cả đường dẫn tương đối `it.path` (và chữ thường) để tra cứu nhanh.
    - Cung cấp hàm `resolveTeamPath(p)` và xuất ra `window.JavisTeams`: `activeTeamId()`, `resolveTeamPath(p)`, `activeTreeKind()`.
  - `dashboard/console.js`:
    - `openVaultPath(fullPath)`: Khi đang ở trang Team (`on-teams`), ưu tiên dùng `resolveTeamPath` để ánh xạ đường dẫn tương đối sang đường dẫn thực tế trong worktree hoặc main-tree.
    - `openNote(rel, it, opts)`: Tự động đính kèm `&team_id=...` khi gọi `/files/read` và `/files/write`. Hiển thị nhãn huy hiệu `[Worktree]` hoặc `[Thư mục chính (read-only)]` trên thanh tiêu đề note; tự động ẩn nút Lưu khi file thuộc main-tree (read-only).
    - `_neTimFileGan`: Đính kèm `&team_id=...` vào `/files/search`. Khi có gợi ý tên gần giống từ worktree hoặc main-tree, hiển thị rõ huy hiệu nguồn tương ứng để bấm mở ngay lập tức.

## 4. Kiểm thử và Xác nhận chất lượng

File kiểm thử chuyên biệt: `tests/python/test_team_worktree_gate.py` với 47 tiêu chí kiểm tra:
1. **Kiểm tra Gate**:
   - `worktree` nằm trong `g.roots`; `main-tree` nằm trong `g.readonly_roots`.
   - Native `Write` và `Edit` trong `worktree`: kết quả `"free"` (thực thi tự do).
   - Native `Write` và `Edit` trong `main-tree`: kết quả `"outside"` (bị chặn, chuyển sang xin duyệt).
   - Native `Write` ngoài cả hai vùng: kết quả `"outside"`.
   - Native `Read` trong `worktree` và `main-tree`: kết quả `"free"`.
2. **Kiểm tra công cụ MCP Hub**:
   - `_safe_read_path` đọc đúng file trong `worktree`, `main-tree` và đường dẫn tuyệt đối.
   - `javis_read_file` đọc thành công nội dung từ `worktree` và `main-tree`.
   - `javis_list_dir` và `javis_search_files` tra cứu thành công trong `main-tree`.
   - `javis_write_file` và `javis_edit_file` thao tác ghi và sửa đổi file trong `worktree` thành công.
   - `javis_write_file` và `javis_edit_file` bị chặn khi cố gắng can thiệp vào file trong `main-tree`, nội dung file gốc được bảo vệ nguyên vẹn.
   - `discover_all` tự động nhận diện `readonly_workspace_root` từ `team_gate.current()`.
3. **Kiểm tra ngữ cảnh xem và tìm kiếm file trên Server (`_safe_serve_path`, `files_read`, `files_write`, `files_search`)**:
   - `_safe_serve_path` resolve chính xác đường dẫn tương đối trong worktree và main-tree khi có `team_id`.
   - `files_read` đọc đúng nội dung file trong worktree, trả về `editable: True`, `readonly: False`, `tree_kind: "worktree"`.
   - `files_read` đọc đúng nội dung file trong main-tree, trả về `editable: False`, `readonly: True`, `tree_kind: "clone"`.
   - `files_write` ghi thành công vào file worktree và cập nhật nội dung trên đĩa.
   - `files_write` trả mã lỗi 403 khi cố gắng sửa file trong main-tree, bảo vệ toàn vẹn origin/main.
   - `files_search` tìm thấy file trong worktree và main-tree khi gõ từ khóa, trả về đúng `tree_kind` và `rel_path`.
4. **Kiểm tra tương thích ngược**:
   - `tests/python/test_team_gate_factory_0207.py` và `tests/python/test_team_runner.py` chạy qua 100% không có bất kỳ lỗi hồi quy nào.
