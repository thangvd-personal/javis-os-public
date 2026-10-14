# Nhóm chat team T3: trưởng nhóm Toàn quyền (0.121.0)

Spec: [2026-10-13-team-chat-design.md](../superpowers/specs/2026-10-13-team-chat-design.md) mục 13, 15.2, 21.3 (D4 tới D6); ca TC31 tới TC36. Chồng trên T2 (PR #639).

## Luồng

1. Chủ mở tab Cài đặt của nhóm, mục **Toàn quyền cho trưởng nhóm**: hộp cấp quyền có phạm vi, trợ lý do nhóm quản lý, provider được Không giới hạn lượt model, nguồn thanh toán, từng trần (giá trị và trần tối đa trưởng nhóm được đặt, mỗi ô có "Không giới hạn"), quyền "Được ngắt việc đang chạy", và các trần luôn áp không bỏ được. Bấm **Bật Toàn quyền**.
2. Lời nhắc của trưởng nhóm có khối "Toàn quyền": trần đang áp, các lệnh quản trị dùng được (khối `JAVIS_TEAM`), giới hạn không vượt được. Ngắt việc chỉ hiện khi chủ cho.
3. Trưởng nhóm trả lời kèm lệnh: mở việc, ưu tiên hay bỏ ưu tiên việc chờ, thêm hay bỏ thành viên, tạo trợ lý mới (thành trợ lý do nhóm quản lý), đặt trần trong mức tối đa, sửa lời nhắc hay bật tắt Cộng hưởng của trợ lý do nhóm quản lý, ngắt việc đang chạy (nếu được cho). Mỗi thao tác hiện thành tin hệ thống trong nhóm và có sự kiện `by=leader:<mã>`.
4. Chủ bấm **Thu hồi** bất cứ lúc nào, hay đổi trưởng nhóm: nhóm về Quyền giới hạn ngay.

## Bản đồ mã

| Phần | File | Việc |
|---|---|---|
| Kho | `server/team_store.py` | Bảng `team_authority` (một dòng hiệu lực mỗi nhóm, `revoked_at`); `grant_authority`, `revoke_authority`, `active_authority` (chỉ khi còn đúng trưởng nhóm), `update_authority_caps` (CAS theo dòng chưa thu hồi), `set_managed` (mỗi agent tối đa một nhóm quản lý), `teams_with_member`; đổi hay bỏ trưởng nhóm thu hồi trong CÙNG giao dịch |
| Bộ chạy | `server/team_runner.py` | `effective_caps` (None = Không giới hạn, D6 theo provider), `_caps` ở mọi chỗ kiểm trần; `grant_authority`, `revoke_authority`, `set_managed`, `authority_view`; `_apply_admin` (chín lệnh) gọi trong `accept_lock` ở pha chấp nhận; khối "Toàn quyền" trong `_context` |
| Host | `server/main.py` (`_TeamAdmin`) | Tạo trợ lý mới không ghi đè (slug `-2`, `-3`), gắn tag "Nhóm"; sửa lời nhắc giữ frontmatter; bật Cộng hưởng ghi `by=leader:<mã>`; cấp mã. Thứ tự khoá: `accept_lock` rồi `_AGENT_META_LOCK` như `delete_agent` |
| Route | `server/team_api.py` | `POST /teams/{id}/authority {mode, grant}`, `POST /teams/{id}/members/{key}/managed {on}`; `GET /teams/{id}` kèm `authority` (dòng, trần hiệu lực, trần không bỏ được), thành viên kèm `managed` |
| Giao diện | `dashboard/teams.js`, `console.css`, i18n | `authorityHtml`, `readGrant`, công tắc "Nhóm quản lý" ở tab Thành viên |

## Quy tắc đã giữ

- Trưởng nhóm không có đường nào tự bật Toàn quyền: chỉ route của chủ (auth, CSRF) gọi `grant_authority`; lệnh lạ trong khối `JAVIS_TEAM` bị bỏ kèm ghi chú.
- "Không giới hạn" lưu là `{"unlimited": true}` trong hộp cấp quyền và trả `None` ở trần hiệu lực; mã kiểm trần coi `None` là không có trần, không thay bằng số lớn (`reserve_turn` nhận `daily_cap=None`, `task_cap=None`).
- Trần trưởng nhóm đặt phải nằm trong trần tối đa chủ đặt; trần tối đa vắng nghĩa là trưởng nhóm không đổi được trần đó; trần tối đa là số thì không đặt được "Không giới hạn".
- D6: Không giới hạn lượt model (`task_turns`, `daily_turns`) chỉ áp khi provider của lượt (suy như adapter: model Claude riêng của agent là `anthropic-cli`, còn lại `aux`) có trong danh sách chủ tích; provider khác dùng trần của nhóm.
- D5: trưởng nhóm chỉ sửa lời nhắc, Cộng hưởng của trợ lý có `managed_by_team` ở nhóm này; sửa trợ lý đang ở nhóm khác thì tin hệ thống liệt kê các nhóm bị ảnh hưởng.
- Thu hồi (13.5): `revoked_at` ghi trong `accept_lock`; lệnh quản trị được áp ở pha chấp nhận, kiểm Toàn quyền TRONG cùng khoá, nên lệnh tới sau mốc thu hồi bị từ chối; nội dung câu trả lời vẫn đăng như ở Quyền giới hạn; trần về mức giới hạn ở lần giữ chỗ kế tiếp, việc vượt trần mới vào `cap_reached`.
- M11: đổi trưởng nhóm (hay bỏ trưởng nhóm khỏi nhóm) thu hồi trong cùng giao dịch, sự kiện `authority_revoked` lý do `leader_changed` hay `leader_removed`.
- Ngắt việc đang chạy chỉ khi `can_interrupt`; lý do ghi sự kiện `job_interrupted` và hiện trong nhóm.
- Không tiến triển ở Không giới hạn: vẫn phát hiện, việc chặn `no_progress`, có tin hộp thư; không chạy tiếp âm thầm.

## Phán quyết và phần chưa làm

- **Trần goal theo nhóm (13.6, các dòng goal) và duyệt phạm vi ghi A4 trong thư mục nhóm (D4): chưa làm.** Lượt nhóm vẫn chỉ chữ (phán quyết R3 của T1): không có đường nào tạo goal hay ghi file từ lượt nhóm, nên các trần đó không có gì để áp. Giá nếu sai: khi mở công cụ cho lượt nhóm phải làm hai phần này cùng lúc.
- Đổi model của trợ lý do nhóm quản lý: chưa làm (chỉ lời nhắc và Cộng hưởng).
- Trần "một lượt mỗi agent cùng lúc" giữ 1, chưa có ô cho chủ đổi.
- Lệnh quản trị dùng khối `JAVIS_TEAM` (phán quyết R1 của T1) thay cho công cụ `javis_team`; `submit` của spec 15.2 không cần vì kết quả là chính câu trả lời.
- Mở việc của trưởng nhóm có khoá chống trùng theo lượt (`leader:<turn_id>:<thứ tự lệnh>`): cùng lượt xử lý lặp không tạo việc thứ hai; hai lượt khác nhau mở cùng tiêu đề là hai việc.
- Chữ của tin hệ thống, ghi chú lệnh bị bỏ chỉ tiếng Việt (như các chữ từ server khác của T1).

## Kiểm

| Lệnh | Kết quả |
|---|---|
| `tests/python/test_team_authority.py` | TC31 tới TC36 với bộ chạy thật, engine giả, host giả: không tự bật, chủ bật, lời nhắc có khối Toàn quyền, thêm thành viên, tạo trợ lý do nhóm quản lý, sự kiện by=leader, tin hệ thống, đổi thứ tự, mở việc; Không giới hạn là null, D6, trần trong trần tối đa; D5 và báo nhóm khác, mỗi agent một nhóm quản lý; thu hồi giữa lượt, cap_reached sau thu hồi, đổi trưởng nhóm thu hồi; ngắt việc cần quyền; không tiến triển ở Không giới hạn |
| `tests/python/test_team_api.py` | thêm ca route `authority`, `managed`, `_TeamAdmin` thật (không ghi đè, giữ frontmatter, Cộng hưởng) |
| `tests/js/test_teams_ui.js` | thêm ca hộp cấp quyền và công tắc Nhóm quản lý |

## Review cuối (người review mới, model mạnh nhất)

Kết luận "Ready with fixes": 0 Critical, 2 Important, 6 Minor. Xếp lại theo tác động:
- **Important:** cờ "do nhóm quản lý" sót lại sau khi bỏ thành viên: nhóm khác không giao quản lý được, chủ không tắt được, thêm lại thì cờ tự sống lại. Nay bỏ hay thêm lại thành viên đều xoá cờ; kiểm "nhóm khác đang quản lý" chỉ tính thành viên đang hoạt động.
- **Important:** D6 suy provider riêng, lệch với engine thật (agent cũ có model Claude mà thiếu provider chạy Claude Code nhưng bị coi là model việc nền, lọt Không giới hạn). Nay provider lấy đúng nhãn `engine_for` trả; chỉ dựng khi nhóm đang Toàn quyền.
- Sửa thêm:
  - trần ngày của tin trò chuyện tính theo provider của người nhận, không chặn nhầm khi trưởng nhóm được Không giới hạn;
  - khoá nguồn việc trưởng nhóm mở không kết thúc bằng số (`_source_id` từng đọc nhầm thứ tự lệnh thành mã tin);
  - sửa lời nhắc trên file không đọc được frontmatter bị từ chối (trước đây xoá tên, vai, model);
  - chủ đặt trần không kèm trần tối đa được, trưởng nhóm không đổi được trần đó;
  - tối đa 10 lệnh quản trị mỗi lượt;
  - form trần ở Cài đặt báo rõ khi Toàn quyền đang bật (trần lấy theo mục Toàn quyền).
- Để lại: lần thu hồi chen giữa lúc đọc Toàn quyền và lúc giữ chỗ lượt có thể cho một lượt chạy theo trần cũ (một lượt, chấp nhận).

Ca review #1 tới #4, #7, #8 trong `test_team_authority.py` đỏ trước rồi xanh; ca #5 (`test_team_api.py`) và #6 (`test_teams_ui.js`) viết sau khi sửa.

**Smoke giao diện** (sandbox cổng 7788, nhóm tạm dừng, trần ngày 0, không mở trần ngày): bật Toàn quyền bằng giao diện với "Không giới hạn" lượt mỗi việc (ô số tự tắt), trạng thái và trần đang áp hiện đúng, tin hệ thống trong nhóm; thu hồi về Quyền giới hạn; 375px không cuộn ngang. Ảnh `exports/reviews/team-t3-smoke/`.
