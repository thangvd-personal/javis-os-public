# Brain Temporary Folder for Chat Uploads & Clipboard Pastes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lưu các tệp đính kèm / văn bản dài dán từ clipboard trên khung chat (`POST /upload`) vào thư mục tạm `<brain_root>/temporary/` của brain hiện tại thay vì chỉ lưu ở `STATE_DIR/.staging`, kèm chú thích rõ ràng trong mã nguồn về lý do kiến trúc của thay đổi này.

**Architecture:**
1. Khi client gửi `POST /upload` kèm tham số `brain` (dashboard luôn gửi `currentBrainPath()`), máy chủ lưu tệp vào `<brain_root>/temporary/` (tự động tạo thư mục nếu chưa có; fallback về `STATE_DIR/.staging` nếu không truyền `brain` hoặc thư mục brain không ghi được).
2. `GET /upload/raw` tìm tệp trong `<brain_root>/temporary/` (theo tham số `brain`, thư mục vừa upload, hoặc các brain trong `BRAINS_DIR`) và fallback về `STATE_DIR/.staging` để giữ tương thích ngược hoàn toàn với các phiên chat cũ và bộ test hiện hữu.
3. Loại trừ `temporary/` khỏi lịch sử Git của brain (`git_brain.py`), khỏi đồ thị tri thức 3D (`graph_builder.py`) và bộ đếm note dropdown (`_count_md`), đồng thời tự động dọn dẹp các tệp quá hạn (`staging_days`, mặc định 3 ngày, xóa cả `.txt`/`.md`) qua `media_gc.py`.

**Tech Stack:** Python 3 (FastAPI/Starlette, pathlib, os), SQLite/Filesystem, JavaScript (Tests).

**Spec:** Approved Option A in conversation (2026-10-16).

## Global Constraints

- Giữ nguyên định dạng `"url": "/upload/raw?name=" + urlquote(...)` của `POST /upload` và chữ ký `_duong_staging(name, thu_muc=None)` (mặc định `STAGING`) để tương thích ngược 100% với `docDinhKem` trên frontend và các canary trong `test_anh_dan_vao_chat.py`, `test_dinh_kem_khong_roi.js`.
- Mọi chú thích (comments/docstrings) bổ sung bằng tiếng Việt rõ ràng, giải thích nguyên nhân gốc rễ (vì sao lưu ở `server/.staging` khiến bộ chuẩn hóa link `_chuan_hoa_link_file`, bộ kiểm tra link `_link_file_khong_thay`, trang Tệp tin và các engine khoá phạm vi brain không truy cập/nhận diện được).
- Tuân thủ TDD (Red-Green-Refactor): viết test thất bại trước khi sửa code nghiệp vụ.

## Review Focus

- Path traversal qua `GET /upload/raw?name=...`: phải chặn mọi dấu `/`, `\`, `..`, null byte và kiểm tra `if f.parent != goc:` sau khi `.resolve()` trên mọi thư mục ứng viên (`<brain>/temporary` lẫn `STAGING`).
- Tương thích ngược khi `POST /upload` không truyền `brain` (`brain=""`): fallback về `STAGING` để không ghi rác vào `Brain Default` thật khi chạy các test cũ chỉ cô lập `JAVIS_STATE_DIR`.
- `media_gc` đối với `<brain>/temporary`: khác với `attachments/` (vốn giữ `.txt` và `.md`), `temporary/` là vùng trung chuyển của khung chat nên phải dọn cả `.txt` (`van-ban-dan-HHMMSS.txt`) và `.md` sau `staging_days` (mặc định 3 ngày), trừ khi tệp đang được ghim vào Project/Session (`giu_path`).
- `_chuan_hoa_link_file` và `_link_file_khong_thay`: khi model dẫn link tuyệt đối hoặc tương đối tới `<brain_root>/temporary/van-ban-dan-HHMMSS.txt`, phải viết lại thành `temporary/van-ban-dan-HHMMSS.txt` và không cảnh báo thiếu file.
- Gitignore & Graph: file `.txt`/`.md` trong `temporary/` không được commit vào Git của brain và không sinh node rác trên đồ thị 3D (`graph_builder.py`).

---

### Task 1: Bộ test TDD cho chu trình `<brain_root>/temporary` (Upload, Raw Serve, Link Normalizer/Verifier, Media GC, Gitignore, Graph)

**Files:**
- Create: `tests/python/test_brain_temporary_upload.py`
- Modify: `server/main.py`, `server/media_gc.py`, `server/git_brain.py`, `server/graph_builder.py`

**Interfaces:**
- Produces:
  - `main.TEMP_DIR_NAME = "temporary"`
  - `main._brain_temp_dir(brain: str = "") -> Path`
  - `main._duong_staging(name: str, thu_muc=None) -> Path`
  - `main._tim_file_upload_tam(name: str, brain: str = "") -> Path`
  - `media_gc.temp_dirs(brain_root: str) -> list[str]`
  - `media_gc.sweep_staging(staging_root: str, max_age_days: int = 3, now=None, giu_path=None) -> dict`

- [ ] **Step 1: Write the failing test `tests/python/test_brain_temporary_upload.py`**
  Kiểm tra toàn diện:
  1. `POST /upload` khi có `brain` lưu file `van-ban-dan-165823.txt` vào `<brain_root>/temporary/van-ban-dan-165823.txt`.
  2. `GET /upload/raw?name=...` phục vụ đúng nội dung file từ `<brain_root>/temporary/` và chặn mọi tên leo thư mục (`../x`).
  3. `_chuan_hoa_link_file` chuyển `[van-ban-dan-165823.txt](<brain_root>/temporary/van-ban-dan-165823.txt)` thành `[van-ban-dan-165823.txt](temporary/van-ban-dan-165823.txt)` và `_link_file_khong_thay` trả `[]` (không báo lỗi thiếu file trong brain).
  4. `media_gc.temp_dirs` nhận diện `<brain_root>/temporary` và `media_gc.sweep_staging` xoá cả file `.txt`/`.md` quá hạn 3 ngày trong `temporary/` (nhưng chừa file trong `giu_path`).
  5. `git_brain._GITIGNORE` chặn `temporary/` và `Temporary/`; `graph_builder.build_graph` và `main._count_md` bỏ qua file `.md` trong `temporary/`.

- [ ] **Step 2: Run test to verify it fails (RED)**
  Run: `python tests/run.py brain_temporary_upload`
  Expected: FAIL vì `POST /upload` vẫn lưu vào `STATE_DIR/.staging` thay vì `<brain_root>/temporary` và các hàm/luật mới chưa có.

- [ ] **Step 3: Implement `<brain_root>/temporary` in `server/main.py`, `server/media_gc.py`, `server/git_brain.py`, `server/graph_builder.py` with detailed explanatory comments**
  - Trong `server/main.py`:
    - Thêm `TEMP_DIR_NAME = "temporary"`, `_RECENT_UPLOAD_DIRS` (nhớ các thư mục `temporary` vừa nhận upload để `/upload/raw?name=...` tra cứu tức thì kể cả với brain ngoài `path:` chưa có phiên trong DB), `_brain_temp_dir(brain)`, cập nhật `POST /upload`, `_duong_staging(name, thu_muc=None)`, `_tim_file_upload_tam(name, brain="")`, `GET /upload/raw`, `_count_md`, và vòng lặp `_start_scheduler` (dọn `temporary/` của từng brain bằng `media_gc.sweep_staging`).
  - Trong `server/media_gc.py`:
    - Thêm `temp_dirs(brain_root)` và tham số `giu_path=None` cho `sweep_staging`.
  - Trong `server/git_brain.py`:
    - Thêm `temporary/` và `Temporary/` vào `_GITIGNORE_BODY` và `untrack_media`.
  - Trong `server/graph_builder.py`:
    - Bỏ qua thư mục `temporary` khi quét `.md` dựng đồ thị tri thức.

- [ ] **Step 4: Run new test and existing test suites to verify all pass (GREEN)**
  Run:
  - `python tests/run.py brain_temporary_upload`
  - `python tests/run.py anh_dan_vao_chat`
  - `python tests/run.py doc_file_dinh_kem`
  - `python tests/run.py media_gc`
  - `python tests/run.py tao_file_tu_chat`
  - `node tests/js/test_dinh_kem_khong_roi.js`
  Expected: ALL PASS.
