# Nhóm chat team T4: lịch sử dài, tìm trong nhóm, đo hiệu năng (0.124.0)

Spec: [2026-10-13-team-chat-design.md](../superpowers/specs/2026-10-13-team-chat-design.md) mục 12.5, 17, 19 (dòng T4); ca TC38. Chồng trên T3 (PR #640).

Bản 0.170.0 (nhóm chat thích nghi T4) thêm phần "Luôn giữ dù lịch sử dài", kiểm vận hành, số đo bằng engine giả và chuỗi quay
về bản cũ: [team-chat-t4-ops.md](team-chat-t4-ops.md), [team-chat-t4-bench.md](team-chat-t4-bench.md).

## Luồng

1. **Tóm tắt phần cũ bằng code.** Khi phiên nhóm dài hơn cửa sổ lịch sử (20 tin, 8.000 ký tự), lượt của thành viên có thêm khối "Tóm tắt phần cũ của nhóm (máy tự tóm, không phải model)":
   - số tin cũ hơn cửa sổ;
   - nhắc rằng quyết định đã ghim vẫn ở Sổ quyết định;
   - tối đa 8 việc đã xong gần đây (mới trước), mỗi việc kèm trích kết quả 160 ký tự.

   Khối chỉ lấy dữ liệu của chính nhóm, tối đa 2.000 ký tự, không gọi model, không mở lượt.
2. **Tìm trong nhóm.** Nút kính lúp ở đầu khung chat mở ô tìm. Cách tìm:
   - khớp mọi từ, không phân biệt hoa thường và dấu tiếng Việt ("mat dien" khớp "mất điện", "deu" khớp "ĐỀU");
   - mới nhất trước, tối đa 50 kết quả;
   - mỗi kết quả có người gửi và đoạn trích quanh chỗ khớp;
   - không bao giờ lẫn nhóm khác.
3. **Nhảy tới tin cũ.** Bấm một kết quả thì tải khúc tin kết thúc ở đúng tin đó và tô sáng nó. Băng "Đang xem tin cũ" có nút **Về tin mới nhất**. Trong lúc xem tin cũ, tin mới tới không bị chèn vào giữa khúc cũ.

## Bản đồ mã

| Phần | File | Việc |
|---|---|---|
| Kho phiên | `server/sessions.py` | `team_message_count`, `fold_text` (bỏ dấu, cả đ, casefold), `team_search` (đọc theo chỉ mục phiên, lọc ở Python vì LIKE của SQLite chỉ bỏ hoa thường cho ASCII; quét tối đa 50.000 tin mới nhất) |
| Bộ chạy | `server/team_runner.py` | `_older_summary` (đặt trước khối lịch sử gần đây trong `_context`), `search` (đoạn trích quanh chỗ khớp); hằng `SUMMARY_JOBS`, `SUMMARY_RESULT_CHARS`, `SUMMARY_CHARS` |
| Route | `server/team_api.py` | `GET /teams/{id}/search?q=&limit=` (câu dưới 2 ký tự 400, brain khác 404); nhảy tới tin dùng lại `GET /teams/{id}/messages?before_ts=&before_id=` |
| Giao diện | `dashboard/teams.js`, `console.css`, i18n | `searchResultsHtml`, `drawSearch`, `runSearch`, `jumpTo`; vùng tìm riêng dưới đầu khung chat (không vẽ lại khi có tin mới) |
| Đo | `tools/team_perf_bench.py` | Dựng state tạm, gọi route thật qua TestClient, in trung vị và p95 kèm máy đo |

## Số đo (máy local, KHÔNG phải VPS)

Máy: Windows 11 (10.0.26200), Intel64 Family 6 Model 165, 8 luồng, Python 3.12.10. Hai loại số đo ghi tách nhau (spec 17). Tệp gốc: `exports/reviews/team-t4-perf/`.

**API phía server** (`tools/team_perf_bench.py`, TestClient trong tiến trình, 20 lần mỗi route):

| Route | 50 nhóm, 5.000 tin: trung vị / p95 (ms) | 1 nhóm, 5.000 tin: trung vị / p95 (ms) |
|---|---|---|
| `GET /teams` | 14,2 / 19,6 | 7,9 / 8,5 |
| `GET /teams/{id}` | 25,4 / 32,3 | 28,1 / 42,8 |
| `GET /teams/{id}/queue` | 14,1 / 16,7 | 16,9 / 26,9 |
| `GET /teams/{id}/messages` (trang đầu 30) | 11,6 / 17,1 | 11,9 / 12,9 |
| `GET /teams/{id}/messages` (200) | 17,0 / 25,4 | 15,7 / 19,2 |
| `GET /teams/{id}/search` | 15,0 / 17,3 | 32,2 / 53,0 |
| `GET /teams/{id}/turns` | 10,3 / 12,4 | 10,0 / 11,5 |

Mở một nhóm chỉ tải trang đầu: 30 tin, `has_more=true`.

**Trình duyệt:** vẽ 200 tin nhóm vào khung chat (dựng HTML, gán `innerHTML`, một lần đọc layout), 10 lần, trên trang Nhóm thật của sandbox, Chromium trong Claude desktop app, khung 1280x860. Trung vị 87,7 ms, chậm nhất 112,3 ms.

**VPS: chưa đo.** Spec 17 và GOAL cần chủ dự án duyệt riêng trước khi chạy trên VPS; các số trên không dùng để kết luận về VPS.

## Phán quyết

- Tóm tắt phần cũ bằng code chỉ dùng việc đã xong và số tin, không tóm nội dung trò chuyện (tóm nội dung cần model). Giá nếu sai: trao đổi cũ không thành việc chỉ còn qua Sổ quyết định và ô tìm của chủ.
- Tìm lọc ở Python (đọc theo chỉ mục phiên) thay cho FTS5: đủ nhanh ở 5.000 tin mỗi nhóm (trung vị 32 ms), không thêm bảng ảo vào kho phiên dùng chung. Giá nếu sai: nhóm có hàng chục nghìn tin sẽ chậm hơn, khi đó thêm FTS5.
- Nhảy tới tin dùng lại con trỏ trang tin có sẵn (khúc kết thúc ở tin đó); chưa có "tải tin mới hơn" từ khúc cũ, thay bằng nút Về tin mới nhất.

## Review cuối (người review mới, model mạnh nhất)

Kết luận "Ready with fixes": 0 Critical, 1 Important, 7 Minor. Sửa cả tám:
- **Important:** đã nhảy tới tin cũ rồi gửi tin, lọc việc thì băng "Đang xem tin cũ" vẫn còn và tin mới (câu trả lời của trợ lý) không được nối vào. Nay mọi lần tải khúc mới nhất đều thoát chế độ xem tin cũ.
- Đoạn trích lệch khi tin gõ tổ hợp (NFD) hay có ký tự đổi độ dài khi casefold: chuẩn hoá NFC rồi gấp từng ký tự có bản đồ vị trí.
- Tìm đọc tới 50.000 dòng đầy đủ trong khoá chung của kho phiên: nay đọc theo lô 2.000 dòng chỉ gồm id và nội dung, dừng khi đủ kết quả, tối đa 20.000 tin; tin đầy đủ chỉ đọc cho tin khớp.
- Câu tóm tắt nói "ở trên" trong khi lịch sử ở dưới; có câu riêng khi tin gần nhất quá dài nên không hiện lịch sử.
- Ô tìm còn lại trên form tạo nhóm; băng "Về tin mới nhất" bị cuộn khuất (nay dính đầu khung); nút tìm hẹp dưới 44px trên điện thoại; kết quả tìm, nhảy tới về muộn sau khi đổi nhóm (nay bỏ).

Ca #2 tới #4 trong `test_team_history.py`, #1, #5 tới #8 trong `test_teams_ui.js` đỏ trước rồi xanh (ca giao diện là kiểm nguồn, chưa soi lại trên trình duyệt).

## Kiểm

| Lệnh | Kết quả |
|---|---|
| `tests/python/test_team_history.py` | tóm tắt (chỉ khi vượt cửa sổ, số tin cũ, việc đã xong mới trước, không lẫn nhóm khác, trần ký tự, không mở lượt), tìm (không dấu, mọi từ, đ thành d, đúng nhóm, đoạn trích quanh chỗ khớp, câu quá ngắn, giới hạn, mới trước), trang đầu 30 tin |
| `tests/python/test_team_api.py` | route tìm: 200, không lẫn nhóm, 400, 404 |
| `tests/python/test_team_perf_bench.py` | script đo chạy được, ghi máy đo và loại số đo |
| `tests/js/test_teams_ui.js` | kết quả tìm escape, nút nhảy, tô sáng, băng tin cũ, CSS `[hidden]`, 44px |

**Smoke giao diện** (sandbox cổng 7788, nhóm tạm dừng, trần ngày 0): tìm "mat dien" ra tin "mất điện"; bấm thì nhảy tới và tô sáng; Về tin mới nhất trở lại khúc mới nhất.
