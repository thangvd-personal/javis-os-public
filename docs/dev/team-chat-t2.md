# Nhóm chat team T2: sáu reaction và vòng phản hồi (0.120.0)

Spec: [2026-10-13-team-chat-design.md](../superpowers/specs/2026-10-13-team-chat-design.md) mục 14; ca TC28 tới TC30, TC37 (phần reaction). Chồng trên T1 (PR #628).

## Luồng

1. Rê chuột vào tin của trợ lý hiện nút mặt cười (máy chạm luôn hiện); điện thoại nhấn giữ tin 0,5 giây cũng mở bộ chọn.
2. Bộ chọn mở ngay tại tin: sáu emoji 👍 ❤️ 😂 😮 😢 😡, sáu lý do chất lượng (chọn được nhiều), ô góp ý tối đa 500 ký tự, một câu nhắc "cảm xúc không phải chấm điểm".
3. Bấm một emoji là lưu ngay; bấm lại emoji đang chọn là bỏ; "Lưu góp ý" lưu lý do và góp ý; "Bỏ phản hồi" xoá hết.
4. Emoji đã chọn hiện thành chip dưới tin (kèm số lý do, "có góp ý"); bấm chip để đổi hay bỏ.
5. Lượt KẾ TIẾP của chính trợ lý đó trong chính nhóm đó có khối "Phản hồi gần đây của chủ nhóm" kèm câu dặn cố định.

## Bản đồ mã

| Phần | File | Việc |
|---|---|---|
| Kho | `server/team_store.py` | `team_reactions` (một dòng mỗi người phản hồi mỗi tin), `team_reaction_log` (nối tiếp, `nonce` UNIQUE); `set_reaction`, `reaction_of`, `reactions_for`, `reaction_log`, `recent_feedback`; hằng `REACTIONS`, `REACTION_EMOJI`, `REACTION_REASONS` |
| Bộ chạy | `server/team_runner.py` | `react` (chỉ ghi kho và đẩy gói WS), `_feedback_lines`, `FEEDBACK_NOTE`; khối phản hồi trong `_context` |
| Route | `server/team_api.py` | `POST /teams/{id}/messages/{mid}/reaction {value, reasons, comment, nonce}`; trang tin kèm `reaction` của từng tin |
| Giao diện | `dashboard/teams.js`, `console.css`, i18n | Bảng `REACTIONS`, `reactionPickerHtml`, `reactionChipHtml`, `nextReaction`; nhấn giữ; giữ bản nháp góp ý qua lần vẽ lại |
| Quy ước | `tests/python/test_icons.py` | Ngoại lệ có lý do cho đúng sáu emoji trong `teams.js`, kèm kiểm cả sáu luôn còn và chỉ nằm trong bảng `REACTIONS` |

## Quy tắc đã giữ

- Đúng sáu giá trị, lưu nguyên (`like`, `love`, `haha`, `wow`, `sad`, `angry`), cộng `none` để bỏ emoji. Lý do và góp ý tồn tại được khi không có emoji.
- Đặt phản hồi là THAY TRỌN trạng thái của người đó trên tin; giao diện tính "bấm lại là bỏ". `nonce` trùng thì không đổi gì.
- Lưu kèm người phản hồi (`owner`), tin, agent tác giả, nhóm, việc, `content_sha` của nội dung lúc bấm.
- Không gọi model, không mở lượt, không đánh thức agent, không đổi việc, goal hay hàng đợi (TC29, kiểm bằng sổ lượt và phiên bản hàng đợi).
- Khối phản hồi: tối đa 5 mục, 30 ngày, mới nhất trước, chỉ tin của chính agent trong chính nhóm; mỗi mục có đoạn trích, emoji gốc kèm tên loại, lý do, góp ý; bỏ phản hồi thì rời khối ở lượt kế (đọc trạng thái hiện tại).
- Không dùng bảng `reactions` của A3.

## Phán quyết

- **Đề xuất bài học từ góp ý để mốc sau** (spec 14.4 mục 4 cho phép): T2 chỉ đưa phản hồi vào ngữ cảnh. Cơ chế "Bài học đề xuất" cần chỗ hiện trên bảng agent, nút nhận và nút gỡ như A3; làm cùng lúc sẽ kéo T2 sang trang Cộng sự. Giá nếu sai: chủ muốn góp ý thành bài học bền thì phải dặn trong chat 1:1 với agent cho tới khi có cơ chế này.
- Người phản hồi chỉ có `owner` (dashboard một tài khoản); cột `reactor` để sẵn.
- Thả cảm xúc được cả ở nhóm đã lưu trữ (chỉ là ghi phản hồi, không mở lượt).
- Nhóm đã lưu trữ hay tin đến muộn (applied=false) vẫn nhận phản hồi; khối phản hồi chỉ đọc theo agent và nhóm.

## Kiểm

| Lệnh | Kết quả |
|---|---|
| `tests/python/test_team_reactions.py` | 31 ca: TC28 (chọn, đổi, bỏ, nonce, nhật ký, giữ qua mở lại kho, từ chối đầu vào sai, chỉ tin của agent đúng nhóm), TC29 (không đổi việc, hàng đợi, không lượt, không engine), TC30 (khối phản hồi, câu dặn, đúng nhóm, đúng agent, bỏ thì rời, tối đa 5, 30 ngày) |
| `tests/python/test_team_api.py` | thêm 6 ca route reaction (200, trang tin kèm reaction, 400, 404 brain khác, không mở lượt) |
| `tests/js/test_teams_ui.js` | thêm 22 ca giao diện reaction |
| `tests/python/test_icons.py` | sáu reaction còn đủ, chỉ trong bảng `REACTIONS`; không emoji trang trí |

**Smoke giao diện** (sandbox cổng 7788, state trong worktree, nhóm tạm dừng, trần lượt ngày 0; tin của trợ lý chèn thẳng vào kho phiên, không gọi model). Ảnh ở `exports/reviews/team-t2-smoke/`:
- desktop: chọn 😢 hiện chip; F5 vẫn còn; thêm lý do "Quá dài" và góp ý; bấm lại 😢 là bỏ emoji, lý do và góp ý còn; đổi sang 😂;
- 375px: nút mặt cười hiện sẵn 44x44; nhấn giữ mở bộ chọn; sáu nút 44x48; không cuộn ngang;
- smoke tìm ra và đã sửa: ở 375px hàng sáu nút tràn mép (😡 bị cắt) vì khe 6px và đệm 10px; chữ "1 reasons" sai ngữ pháp (đổi thành "Reasons: 1");
- sổ lượt của nhóm sau smoke: 0 lượt.

## Review cuối (người review mới, model mạnh nhất)

Kết luận "Ready with fixes": 1 Important, 6 Minor. Xếp lại theo tác động, sửa cả bảy:
- **Important:** chi tiết việc (khung lọc theo việc) trả tin thiếu phản hồi, bấm emoji ở đó xoá mất lý do và góp ý đã lưu. Route `GET /teams/{id}/tasks/{task}` nay kèm `reaction` của từng tin.
- Góp ý nhiều dòng phá khuôn một dòng mỗi mục (giả được đầu mục mới): gộp khoảng trắng.
- Phản hồi của tin không còn vẫn vào khối với đoạn trích rỗng: bỏ qua.
- Đang soạn góp ý mà có tin mới tới thì mất con trỏ, bị kéo xuống cuối: giữ con trỏ, chỗ cuộn.
- Mở bộ chọn không đưa focus vào bảng (Escape không đóng được với bàn phím): focus emoji đầu, đóng thì trả focus về nút mặt cười.
- Nhấn giữ chuột cũng mở bộ chọn, và đè menu nhấn giữ của máy chạm: chỉ chạm và bút; chặn menu ngữ cảnh của lần giữ đó; `-webkit-touch-callout: none` trên máy chạm.
- Nonce đã dùng cho tin khác trả 200 "đã lưu": nay 400; nonce dài quá 100 ký tự bị từ chối.

Bốn lỗi phía server có test đỏ trước rồi xanh (`test_team_reactions.py`, `test_team_api.py`). Ba lỗi giao diện kiểm bằng test nguồn trong `test_teams_ui.js` viết sau khi sửa (hành vi DOM chưa chạy được dưới node; chưa soi lại trên trình duyệt).

Phần reviewer không kết luận: nhấn giữ trên iOS Safari, Android Chrome thật; trình đọc màn hình đọc chip; `de.json` chưa có khoá `teams.*` (rơi về tiếng Anh, có từ trước T2).
