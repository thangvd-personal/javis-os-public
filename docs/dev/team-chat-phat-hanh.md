# Nhóm chat team: tích hợp T0 tới T5 vào main, phát hành 0.140.0

Ngày 14/10/2026. Nhánh tích hợp `claude/team-chat-release`, PR [#649](https://github.com/blogminhquy/javis-os/pull/649).
Spec: [2026-10-13-team-chat-design.md](../superpowers/specs/2026-10-13-team-chat-design.md). Biên bản từng mốc:
`team-chat-t1.md` tới `team-chat-t5.md` (số phiên bản ghi trong các biên bản đó là số GIỮ CHỖ lúc làm, chưa từng phát hành).

## Chuỗi và cách tích hợp

Sáu PR chồng nhau, thẳng, không merge commit, cùng nền `bfb1c29f` (0.109.0):

| Mốc | PR | Head | Số giữ chỗ cũ | Commit trên nhánh tích hợp |
|---|---|---|---|---|
| T0 công tắc Cộng hưởng + tài liệu thiết kế | #627 | `83a1be54` | 0.110.0 | `4ddf0aa3` |
| T1 lõi nhóm chat | #628 | `cee0159d` | 0.111.0 | `14be8af8` |
| T2 sáu reaction và góp ý | #639 | `a82c0d05` | 0.120.0 | `97c05db4` |
| T3 Toàn quyền của trưởng nhóm | #640 | `c9f4d97c` | 0.121.0 | `5b76feac` |
| T4 lịch sử dài, tìm, đo | #643 | `bc7d653b` | 0.124.0 | `68cf5821` |
| T5 trang Nhóm gọn, ảnh thật, ghim | #645 | `95db0f57` | 0.129.0 | `359e9293` |

- **Cách làm:**
  - main đã đi thêm 38 commit (tới 0.137.0, `24cfe57c`). Nhánh tích hợp bắt đầu từ `origin/main`.
  - Áp phần thay đổi của từng PR so với PR trước đúng thứ tự phụ thuộc (`git diff prev..head`, `git apply --3way`), mỗi PR một commit, BỎ các commit giữ chỗ `VERSION` và CHANGELOG cũ.
  - Không đưa lại commit nào đã có trên main: main chưa chứa commit nào của chuỗi.
- **Xung đột văn bản:** không có. Số dòng thêm, bớt của từng file trong 61 file trùng khít giữa "chuỗi so với nền cũ" và "nhánh tích hợp so với main".
- **Bốn file main cũng đã sửa từ nền chung,** kiểm riêng từng file:
  - `dashboard/i18n/vi.json`, `en.json`: gộp đủ, hai bên cùng 3998 khoá, đúng định dạng.
  - `server/main.py`: phần đăng ký `/teams` vẫn nằm sau route cuối của main.
  - `tests/python/route_table.json`: chụp lại. 422 route của main giữ nguyên thứ tự, 25 route `/teams` nối sau.
- **Số phát hành 0.140.0:**
  - Lúc bắt đầu, main ở 0.137.0 và PR mở khác giữ số thấp hơn, nên em giữ chỗ 0.138.0 (commit đầu nhánh và PR nháp #649).
  - Trong lúc tích hợp, #644 phát hành thành 0.139.0 (`be1f8aa2`), vượt số đã giữ. Theo quy ước, nhánh được đưa lên main mới bằng rebase: bỏ commit giữ chỗ cũ, giải xung đột CHANGELOG (giữ khối 0.139.0 của main, đặt khối nhóm chat lên trên).
  - Đánh số lại thành 0.140.0, và PR #649 đổi tiêu đề sang 0.140.0 ngay khi phát hiện.
  - Các commit trong bảng trên là SHA sau rebase. Thân của vài commit còn ghi "0.138.0" là số lúc làm.

## Sửa khi phát hành

- **Nhãn snapshot đổi từ `.pre-0.111.0` thành `.pre-0.140.0`.** Áp cho `team_migration.py`, công cụ `tools/team_chat_restore_pre.py` (`.post-0.140.0-<thời điểm>`), chú thích ở `sessions.py`, `task_store.py`, `ui_targets.py`. Vì sao: 0.111.0 chưa từng phát hành, người dùng sẽ thấy file mang một số lạ.
- **Phép thử quay về chạy mã 0.139.0 thật** (`be1f8aa2`, bản phát hành cuối trước nhóm chat; lúc đầu là 0.137.0 `24cfe57c` trước khi main đi tiếp) thay cho 0.109.0. Áp cho `test_team_rollback.py` và bước fetch trong `ci.yml`. Mã cũ mở được kho đã nâng, không nhặt việc nhóm; công cụ khôi phục đưa về đúng snapshot (31 ca).
- **Test mới `test_team_release.py`:** kho nhóm đời T1 (lược đồ chép nguyên văn, chưa có `pinned_at`, `identity_json`, `committed_at`) mở bằng mã phát hành thì tự thêm cột. Dữ liệu cũ giữ nguyên, ghim và chụp nhân dạng chạy được, mở lại lần hai không lỗi.
- **CHANGELOG một khối 0.140.0** ngắn theo điều người dùng thấy; các khối giữ chỗ 0.110.0 tới 0.129.0 không đưa lên main.

## Review toàn bộ kết quả tích hợp

Một người review mới (model mạnh nhất) soi cả `origin/main..HEAD`, không chỉ các bản sửa cuối. Kết luận "Ready with fixes": 2 Important, 8 Minor.

**Đã kiểm, không có lỗi:**
- Mọi hàm của main mà mã nhóm gọi vẫn có, chữ ký tương thích.
- Phần nhóm trong `main.py` không chồng với 38 commit của main. Tính năng mới của main (Zalo trả lời khi chủ im, `agy update`, câu trả lời khi lượt lỗi) không bị đụng.
- i18n gộp đúng; route nhóm nằm sau route cuối; nhãn snapshot thống nhất.
- Việc nhóm tách hẳn khỏi bảng Việc (mọi truy vấn lọc theo làn).

Sửa, mỗi mục có test chạy đỏ trước:

| Mục | Sửa | Test |
|---|---|---|
| Important: bấm thư về việc nhóm bị chặn thì mở vào khung chat chính, gõ tiếp thành tin không tác giả trong phiên nhóm | Thư kênh `team:<id>` mở đúng nhóm ở trang Nhóm qua `JavisTeams.open`; chưa ở trang Nhóm thì sang trang rồi mở khi danh sách về | `test_teams_ui.js`; smoke trình duyệt (dưới) |
| Important: `conversations.db.pre-0.140.0`, `.post-0.140.0-*` không bị gitignore, chép toàn bộ chat có thể lên git | `.gitignore` thêm `server/conversations.db.pre-*`, `.post-*` | `test_team_release.py` (`git check-ignore` cho cả ba kho) |
| Nâng lên Important: bí danh giọng nói "nhom" khớp cụm con, nên "nhóm việc" mở trang Nhóm thay vì Việc (cả "nhóm code", "mở nhóm trợ lý", "nhóm hệ thống") | "nhom" chỉ khớp khi cả câu đúng là nó; bỏ "nhom tro ly" | `test_team_release.py`: câu về nhóm thanh bên ra đúng như bản trước phát hành |
| Nâng lên Important: lời nhắc chung của trợ lý hứa "bộ công cụ của hub" mà lượt nhóm chỉ chữ, gọi công cụ là hỏng lượt | Mọi lượt nhóm nối thêm đoạn cố định `TEXT_ONLY_NOTE` sau lời nhắc của trợ lý | `test_team_ux.py` |
| Minor: nhắc việc chặn quá 24 giờ chỉ tra 500 sự kiện cuối, nhóm bận bị nhắc lặp | `TeamStore.has_reminder` tra thẳng trong kho | `test_team_ux.py`: hơn 600 sự kiện, không nhắc lặp |
| Minor: việc nền của API nhóm không được giữ tham chiếu | `TeamApi._bg` giữ tới khi xong | `test_team_ux.py` |
| Minor: tạo kho, bộ chạy lười không khoá giữa các luồng | `RLock` khi tạo | |
| Minor: chú thích `console.js` còn "0.111.0" | sửa | `test_teams_ui.js` |

**Để lại** (ghi rõ, không sửa trong bản này):
- Server chưa chặn lượt của khung chat chính trên phiên `team:`. Đường UI dẫn tới đó đã đóng, và tìm kiếm đã lọc phiên nhóm từ T1.
- Lần đầu chạy 0.140.0 chụp đồng bộ một bản `conversations.db` (chưa đo trên kho lớn; bản chụp không tự dọn).
- Phiên nhóm lưu tên brain thô (`brain`, không qua `_brain_key`); hiện không ảnh hưởng chức năng.

## Kiểm

**Máy local** (`.venv` dự án, SQLite tạm, engine giả, không gọi model):
- Biên dịch `server`, `system/plugins`; nạp `main` được.
- `tests/run.py --js`: 186/186.
- `tests/run.py` toàn bộ Python: 678/692. 14 file đỏ, không file nào thuộc nhóm chat:
  - 12 file nằm trong danh sách đỏ sẵn trên máy này (agy, antigravity_cli, grok_cli, terminal, windows_no_console, cai_windows, install_admin, link_file_uri, memory_hoa_thuong, model_theo_phien, ollama_local, ba_loi_mac_va_telegram);
  - `test_machine_translations` đọc các file dịch mà nhánh không đụng tới (giống hệt main);
  - `test_project_khung` chạy riêng thì xanh.
- Sau đợt sửa review: các test `team*`, `route_table`, `ui_targets`, `ui_bridge` 20/20; `test_team_rollback` 31 ca với mã 0.137.0 thật (chạy lại với 0.139.0 sau khi đánh số lại, mục dưới).

**Smoke trình duyệt** (sandbox cổng 7788, trần ngày 0):
- Bật lại nhóm "Ra mắt sản phẩm": ba việc chờ bị chặn ngay ở bước giữ lượt, ba thư kênh `team:` vào hộp thư.
- Ở trang chủ bấm chuông rồi bấm thư: sang trang Nhóm, mở đúng nhóm, có nút Cho thêm lượt.
- Ba nhóm đều 0 lượt, 0 lần gọi engine; nhóm được tạm dừng lại sau khi thử.
- Ảnh: `exports/reviews/team-release-0.138.0/chuong-mo-dung-nhom.jpg`.

**CI:**
- `9d8ce139` (commit phát hành, trước khi đánh số lại): xanh cả push và pull_request.
- `eb85f38b` (sau đợt sửa review, trước khi đánh số lại): xem mô tả PR #649.
- HEAD cuối (sau khi đưa lên 0.139.0 và đánh số 0.140.0): ghi ở mô tả PR #649 và báo cáo phát hành.
