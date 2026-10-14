# Nhóm chat team T5: trang Nhóm gọn như app chat (0.129.0)

Chồng trên T4 (PR #643). Nguồn: buổi soi giao diện ngày 14/10/2026 (ảnh trong `exports/reviews/team-ux-audit/`), chủ dự án duyệt hướng làm và bảo làm cả ba bước một lèo. Không đổi luồng nhóm của T1 tới T4; chỉ đổi cách trình bày, chữ, và thêm một nút "Cho thêm lượt".

## Vấn đề lúc soi (điện thoại 375px)

- Khung tin chỉ còn 284px (35% màn hình). Nó bị ép bởi:
  - đầu trang chung;
  - đầu khung chat hai hàng;
  - ô lọc việc;
  - băng tạm dừng;
  - khung soạn 5 hàng (209 tới 249px).
- Tên thành viên vỡ mỗi chữ một dòng.
- Tab Cài đặt dài 2.003px với 31 ô nhập.
- Khung soạn có hai lỗi:
  - nút bật Giao việc và nút gửi cùng chữ "Giao việc";
  - người được @ hiện hai lần (chip và chữ).
- Việc bị chặn vì hết lượt có nút "Cho chạy tiếp" nhưng bấm xong chặn lại ngay, vì trần chưa nâng.
- Chữ nội bộ lộ ra người dùng: trần, lượt model, Toàn quyền, Quyền giới hạn, vị trí hàng.
- Linh vật che khung soạn; tiêu đề "Nhóm" lặp hai lần.
- Máy tính: khung chat chỉ rộng 564px vì cột cài đặt luôn mở.

## Bố cục mới

1. **Trang chiếm trọn chiều cao.**
   - `console.js` thêm `.cview-flush` và `body.on-teams`; `_pageLeave` gỡ cả hai.
   - Trên điện thoại, đầu trang chung và linh vật ẩn.
   - Khung soạn luôn ở đáy, chừa vùng an toàn đáy máy.
2. **Danh sách nhóm kiểu app chat.**
   - Mỗi dòng có ảnh chữ đầu (màu cố định theo tên), tên, giờ, dòng xem trước "Lan: ..." hay "Bạn: ...", và nhãn Cần bạn / Đang làm / Tạm dừng / Lưu trữ.
   - Chưa có nhóm thì hiện thẻ hướng dẫn ba bước kèm nút Tạo nhóm.
3. **Đầu khung chat một hàng:** quay lại, tên nhóm và "Lan dẫn nhóm · 3 người", chip việc, tìm, ba chấm.
   - Chip việc ghi "Cần bạn N" (màu cảnh báo) hay "Việc N"; trên điện thoại chỉ còn icon kèm số.
   - Bỏ ô chọn lọc tin. Lọc theo việc giờ là một băng "Đang xem tin của việc X" kèm nút Xem tất cả.
4. **Băng báo gọn có nút:** tạm dừng thì Bật lại, chưa có trưởng nhóm thì Chọn.
5. **Dải việc ở đầu khung chat.**
   - Việc đang làm hiện một dòng, kèm Sửa yêu cầu và Huỷ.
   - Việc chờ bạn hiện tối đa hai thẻ, mỗi thẻ một nút giải quyết theo lý do (bảng dưới); còn nhiều hơn thì gom thành một nút.
6. **Tin dạng bong bóng.**
   - Tin của chủ ở bên phải, không tên.
   - Tin của trợ lý ở bên trái, kèm ảnh và tên.
   - Nhãn vai chỉ hiện cho bước của việc.
   - Trạng thái "Xong" ẩn; câu trả lời không được dùng thì hiện một dòng cảnh báo.
   - Tin hệ thống là dòng xám ở giữa.
   - Nút cảm xúc (tin trợ lý) hay ba chấm (tin của chủ) nằm cạnh bong bóng, không thêm hàng.
   - Nhấn giữ mở bảng cho mọi tin. Ghim nằm trong bảng; tin đã ghim có nhãn Quyết định, bấm nhãn để bỏ ghim.
7. **Khung soạn một hàng:** công tắc Giao việc (`aria-pressed`), ô chữ tự cao tới khoảng năm dòng, nút gửi tròn.
   - Người được gọi là các "@Tên" còn trong chữ lúc gửi: xoá chữ là bỏ gọi, không còn chip trùng.
   - Chỉ khi bật Giao việc mới có một dòng "Sẽ tạo một việc cho ...".
8. **Ngăn "Việc | Nhóm" đóng sẵn**, mở từ chip việc hay ba chấm.
   - Hiện thành cột phải trên máy tính, ngăn kéo có nền mờ dưới 1060px, trang trượt lên dưới 760px.
   - Escape hay chạm nền mờ thì đóng, focus về lại đúng nút đã mở.
   - **Tab Việc:** Đang làm, Chờ bạn, Sắp làm (đánh số, Ưu tiên, Huỷ).
   - **Tab Nhóm:**
     - thành viên mỗi người một dòng, chạm để mở thao tác;
     - "Trưởng nhóm tự quyết đến đâu?" với ba mức;
     - "Hôm nay đã dùng X/Y lần trả lời";
     - tên, mô tả, trạng thái;
     - "Nâng cao" (đóng sẵn) chứa số giới hạn, hộp quyền chi tiết của T3 và sổ lần trả lời.

9. **Ảnh đại diện đúng ảnh trợ lý và ghim nhóm** (chủ dặn thêm cùng ngày, mẫu tham khảo: Zalo, "Offsite crew").
   - Mọi chỗ vẽ trợ lý (tin, danh sách thành viên, gợi ý @) dùng bộ `JavisAvatar` của trang Cộng sự, theo slug và `avatar` trong file trợ lý. `_resonance_agents_meta` kèm `avatar`; `member_views` trả `avatar` cho từng thành viên.
   - Ảnh nhóm ghép từ ảnh thành viên đang hoạt động (trưởng nhóm trước): 1 người một ảnh, 2 người chéo, 3 người tam giác, 4 người lưới 2x2. Hơn 4 người thì ba ảnh và ô "+N" (quá 99 ghi "99+"). `GET /teams` kèm `faces` (tối đa 4) và `member_count`; đầu khung chat suy từ thành viên.
   - Ghim: cột `pinned_at` trong `teams` (tự thêm cho kho cũ), `TeamStore.set_pinned` (sự kiện `pinned` / `unpinned`), route `POST /teams/{id}/pin {on}`. Trang đầu của danh sách mở bằng các nhóm đã ghim (ghim sau đứng trước), trang sau chỉ gồm nhóm không ghim nên không lặp. Dòng nhóm có dấu ghim; công tắc "Ghim nhóm lên đầu danh sách" nằm trong tab Nhóm.

10. **Ghim ngay trên danh sách** (chủ duyệt sau khi hỏi "có cần ghim bằng nhấn giữ không").
    - Công tắc trong tab Nhóm vẫn là lối dễ thấy; đây là lối tắt quen tay của người dùng Zalo, Messenger.
    - Máy tính: rê chuột lên dòng nhóm thì hiện nút ghim ở góc dòng. Nút này là anh em của nút mở nhóm, không lồng nút.
    - Chuột phải lên dòng mở menu "Ghim nhóm" hay "Bỏ ghim".
    - Điện thoại: nhấn giữ dòng nhóm 0,5 giây mở cùng menu đó. Cú bấm sinh ra lúc nhấc tay bị chặn (`swallowClick`, xoá ở lần chạm sau) nên không mở nhầm nhóm. Máy chạm không có nút ghim nổi.
    - Menu đóng bằng Escape (focus về dòng nhóm), bấm ra ngoài, hay sau khi chọn.
    - Ghim từ danh sách dùng chung `savePin`: lỗi thì báo, không kẹt.
    - Smoke trên sandbox: rê chuột rồi bấm nút ghim; chuột phải rồi Bỏ ghim; Escape; nhấn giữ bằng sự kiện chạm giả lập (menu mở, không mở nhầm nhóm); chạm ngắn vẫn mở nhóm.

## Nút giải quyết việc bị chặn

| Lý do chặn | Nút |
|---|---|
| `cap_reached` | Cho thêm lượt (route mới `extend`) |
| `failed`, `no_progress` | Thử lại (chạy lại bước) |
| `interrupted` | Làm tiếp (chạy lại bước) |
| `no_leader` | Chọn trưởng nhóm (mở tab Nhóm) |
| `agent_busy_long`, `waiting_user` | Cho chạy tiếp |
| `assignee_removed` | chỉ Huỷ |

## Ba mức tự quyết (đặt lên Toàn quyền của T3)

| Mức | Gửi tới `POST /teams/{id}/authority` |
|---|---|
| 1 Hỏi tôi trước | `mode: limited` (thu hồi) |
| 2 Tự làm, báo tôi | `ceilings {task_turns 24, task_steps 12, help_depth 3, daily_turns 200}`, không ngắt việc, không provider Không giới hạn, gói thuê bao |
| 3 Tự quản cả nhóm | mọi trần tối đa Không giới hạn, được ngắt việc, không provider Không giới hạn (hỏi lại trước khi bật) |

- `levelOf` đọc ngược hộp quyền và chỉ so trần tối đa, quyền ngắt việc, provider. Trưởng nhóm tự chỉnh số trong khoảng cho phép thì vẫn giữ mức.
- Hộp quyền khác ba mức hiện là "Tuỳ chỉnh"; sửa chi tiết trong Nâng cao.

## Bản đồ mã

| Phần | File | Việc |
|---|---|---|
| Bộ chạy | `server/team_runner.py` | `_block_cap` (ghi sự kiện `cap_blocked` kèm trần đã chạm và các bước bị từ chối, rồi chặn), `_put_steps`, `extend_job`, `usage`; lý do chặn và tin hệ thống bằng lời thường (`CAP_PLAIN`, `EXTEND_TEXT`) |
| Kho nhóm | `server/team_store.py` | `update_authority_caps(..., ceilings=)` để chủ nâng cả trần tối đa |
| Route | `server/team_api.py` | `GET /teams` kèm `last`; `GET /teams/{id}` kèm `usage`; `POST /teams/{id}/tasks/{task}/extend` (404 việc nhóm khác, 409 việc không chặn vì hết lượt) |
| Giao diện | `dashboard/teams.js`, `console.css` (khối `tm-*` cuối file), `console.js`, i18n vi và en | như trên |

## Cho thêm lượt (`extend_job`)

Toàn bộ chạy trong `accept_lock`, nên hai lần bấm cùng lúc chỉ một lần có tác dụng.

1. Đọc bản ghi `cap_blocked` mới nhất của việc. Bản ghi được ghi TRƯỚC khi chặn, nên đó là của lần chặn hiện tại.
2. Trần đó còn chặn (theo số đã dùng thật) thì nâng nó lên `max(trần, số đã dùng) + nấc`. Nấc: lần trả lời trong ngày +20, lần trả lời mỗi việc +6, bước +4.
   - Hộp quyền đặt số cho trần đó thì nâng trong hộp quyền (và nâng trần tối đa nếu thấp hơn).
   - Không thì nâng trần của nhóm.
3. Trần đó không còn chặn (sang ngày mới, chủ đã tự nâng) thì không nâng gì.
4. Thêm lại các bước bị từ chối. Bước tiếp nối sau khi nhờ hỗ trợ được nối lại đúng như lúc thêm bình thường.
5. Việc về hàng chờ.
6. Có sự kiện `caps_extended` và một tin hệ thống bằng lời thường.

Việc chặn từ trước khi có bản ghi thì đoán theo số thật. Trần bước chỉ được đoán khi không có bước nào đang chờ, vì chặn do lượt thì bước luôn về hàng chờ.

## Phán quyết

- **Nâng từ số đã dùng chứ không từ trần.**
  - Vì sao: trần bị hạ dưới số đã dùng thì nâng từ trần vẫn chặn lại ngay.
  - Giá nếu sai: một lần bấm có thể cho nhiều hơn một nấc so với trần cũ.
- **Cho thêm lượt khi trần ngày là 0 (chủ cố ý tắt) vẫn nâng lên 20.**
  - Vì sao: đây là một cú bấm rõ ràng của chủ, có tin hệ thống ghi lại.
  - Giá nếu sai: nút này vượt được "công tắc tắt" bằng trần 0.
- **Nhóm tạm dừng vẫn cho thêm lượt.**
  - Việc về hàng chờ và câu báo nói rõ phải Bật lại nhóm.
- **Mức 3 là "trần tối đa Không giới hạn" chứ không phải "lượt model Không giới hạn".**
  - Không provider nào được Không giới hạn (D6), nên trưởng nhóm phải tự đặt số.
  - Mô tả nói đúng điều đó.
- **Linh vật chỉ ẩn trên điện thoại.**
  - Trên máy tính nó nằm ở mép phải như mọi trang khác, có lúc đè mép ngăn Nhóm.

## Review cuối (người review mới, model mạnh nhất)

Kết luận "Not ready": 1 Critical, 5 Important, cùng các Minor. Em sửa hết Critical và Important, cộng hai Minor nâng lên vì người dùng chịu hậu quả thật, và vài Minor rẻ.

- **C1. Chặn vì trần bước làm mất các bước bị từ chối.** Cho thêm lượt xong, việc thành `failed` ("Không còn bước nào để chạy").
  - Nay các bước bị từ chối được ghi trong sự kiện `cap_blocked` và thêm lại khi cho thêm lượt.
- **I1. Trần đã chạm không còn chặn thì nâng nhầm trần bước.**
  - Nay đọc trần từ bản ghi lúc chặn, và không nâng gì nếu trần đó không còn chặn.
- **I2. Trần bị hạ dưới số đã dùng thì cho thêm vẫn chặn lại ngay.** Nay nâng từ số đã dùng.
- **I3. Ngăn kéo ở khổ 761 tới 1060px nằm dưới thanh trên toàn cục**, nên hàng tab và nút đóng bị che.
  - Nay bắt đầu từ `--topbar-h`; trang trượt lên cao theo khung nhìn thật.
- **I4. Mô tả mức 3 hứa bỏ giới hạn trong khi server vẫn áp trần nhóm.** Đã viết lại.
- **I5. Tin của chủ đã ghim mở một bảng trống**, và từ đó tin mới không tự cuộn xuống.
  - Nay bỏ nút ba chấm và không mở bảng cho tin đó.
  - `drawMsgs` cũng bỏ trạng thái mở khi bảng không vẽ ra.
- **Nâng lên Important:**
  - địa chỉ email "ho@lan.vn" bị coi là gọi Lan (tốn một lượt model); nay "@" phải đứng đầu chữ hay sau ký tự không phải chữ, và so trên chữ NFC;
  - đang gõ trong tab Nhóm mà có sự kiện nhóm thì ngăn vẽ lại và mất chữ; nay không vẽ lại khi đang gõ trong ô chữ.
- **Minor đã sửa:**
  - Cho thêm lượt lỗi không còn để nút kẹt tắt;
  - đổi mức lỗi thì lựa chọn quay về mức thật;
  - sổ lần trả lời về muộn sau khi đổi nhóm thì bỏ;
  - ba mức là một nhóm lựa chọn có tên (fieldset + legend);
  - nút mở ngăn có `aria-expanded`, thân ngăn là `tabpanel`;
  - công tắc thành viên và nút chọn trạng thái đủ 44px;
  - nhãn việc dài cắt bằng dấu ba chấm;
  - bỏ 9 khoá chữ không còn dùng.

Ca đỏ trước rồi xanh:
- `test_team_ux.py`: C1, I1, I2.
- `test_teams_ui.js`: các ca "review T5".

Hai ca CSS (I3, 44px) em sửa trước khi viết test nên không thấy đỏ.

## Review head `b93c46bd` (biên bản `exports/reviews/PR-645-b93c46bd-review.md`)

Ba lỗi P2, sửa hết. Mỗi lỗi có test tái hiện chạy đỏ trước rồi xanh.

1. **Công tắc ghim kẹt khi lưu lỗi.** HTTP khác 200 hay lỗi mạng để công tắc ở trạng thái chưa lưu và bị tắt, lỗi mạng còn bị bỏ sót.
   - Nay `savePin` khoá công tắc trong lúc lưu. Lỗi thì trả về trạng thái đã lưu, bật lại để thử lại, báo lỗi (lỗi mạng có câu riêng).
   - Test hành vi với công tắc giả: HTTP 500, fetch bị từ chối (không có unhandled rejection), lưu được.
2. **Tin cũ mất ảnh và tên khi trợ lý rời nhóm.** Ảnh tin tra theo thành viên đang hoạt động.
   - Nay `GET /teams/{id}` trả thêm `authors` {agent_key: {slug, name, avatar}} của mọi trợ lý từng ở nhóm (`members(include_removed=True)`, tra danh tính theo agent_key).
   - Tin tra tác giả theo thành viên rồi tới sổ này; kết quả tìm cũng vậy.
   - Ảnh nhóm và gợi ý @ vẫn chỉ lấy thành viên đang hoạt động.
   - Test: route sau khi bỏ, xoá rồi tạo lại trợ lý; tin của tác giả không còn trong nhóm.
3. **Cho thêm lượt mất các bước bị từ chối khi bản ghi chặn trôi khỏi 500 sự kiện cuối của nhóm.**
   - Nay `TeamStore.cap_record(team_id, task_id)` tra thẳng bản ghi `cap_blocked` mới nhất của đúng việc trong SQLite (`json_extract`), không giới hạn số sự kiện.
   - Test: chặn vì trần bước, thêm một bản ghi của việc khác, rồi 600 sự kiện ghim / bỏ ghim. Sau đó cho thêm lượt thì thêm lại đủ hai bước, việc chạy tới xong.

Kiểm:
- `tests/run.py --js` 186/186.
- `test_teams_ui.js` 184 mục.
- `tests/run.py team agent_avatar agents_danh_sach_nhe resonance_mvp_main route_table icons` 24/24.

SQLite tạm, engine giả, không gọi model.

Sửa số liệu: biên bản và báo cáo trước ghi test giao diện "190 mục". Thật ra lúc đó là 178 mục.

## Review head `4f6268bd` (biên bản `exports/reviews/PR-645-4f6268bd-review.md`)

Hai lỗi P2, sửa hết. Mỗi lỗi có test tái hiện chạy đỏ trước rồi xanh.

1. **Tin cũ nhận tên và ảnh của trợ lý mới dùng lại slug.**
   - **Lỗi:** sổ tác giả lấy tên và ảnh theo slug của file hiện tại. Xoá Minh rồi tạo Minh mới cùng slug thì mã cũ cũng thành "Minh mới".
   - **Ảnh chụp nhân dạng:** nhân dạng giờ giữ theo agent_key (`TeamApi._identity`). File hiện tại chỉ được dùng khi dòng danh tính của đúng mã đó còn `active`; mã đã nghỉ hay đang `missing` thì không. Khi xác nhận được, nhân dạng được chụp vào cột mới `team_members.identity_json` (tự thêm cho kho cũ; chỉ ghi khi đổi).
   - **Không xác nhận được:** dùng ảnh chụp gần nhất của chính mã đó.
   - **Chưa từng chụp (bản ghi cũ):** dự phòng theo chính mã, tức tên theo slug lúc vào nhóm và ảnh suy từ mã (`avatar_seed`, dashboard vẽ theo mã chứ không theo slug). Không bao giờ lấy tên hay ảnh của trợ lý mới.
   - **Test:** Minh cũ (ảnh mặc định) bị xoá; Minh mới cùng slug có hình và màu khác. Mã cũ giữ đúng "Minh" và ảnh cũ, mã mới là "Minh mới" với ảnh mới. Thêm ca xoá ảnh chụp thì mã cũ rơi về dự phòng của chính nó.
2. **Lỗi ghim từ danh sách không nhìn thấy trên điện thoại.** Vùng thông báo nằm trong khung chat, mà khung chat bị ẩn khi đang ở danh sách.
   - **Toast chung:** vùng thông báo `.tm-note` chuyển ra ngoài khung chat thành lớp nổi trên cả trang (`role="status"`, nền đặc). Có nút Thử lại khi thao tác chạy lại được, có nút đóng; tự ẩn sau 8 giây, có Thử lại thì 15 giây.
   - **Ghim từ danh sách:** đi qua `pinTeamWith(id, on, btn, deps)`. Lỗi HTTP hay mất mạng thì báo kèm Thử lại chạy lại đúng thao tác. Mất mạng có câu riêng `teams.err_network` thay chữ "network", dùng cả khi gửi tin.
   - **Test:** HTTP 500, fetch bị từ chối (không có unhandled rejection), Thử lại rồi lưu được. Kiểm thêm thông báo nằm ngoài khung chat và CSS không ẩn nó ở danh sách.
   - **Smoke trình duyệt** ở 375px, đang ở danh sách: giả lập POST /pin trả 500 rồi mất mạng, thông báo nhìn thấy kèm Thử lại; Thử lại khi mạng ổn thì ghim được. Ảnh `exports/reviews/team-t5-smoke/m08-ghim-loi-mang-danh-sach-375.jpg`.

Kiểm:
- `tests/run.py --js` 186/186.
- `test_teams_ui.js` 200 mục.
- `tests/run.py team agent_avatar agents_danh_sach_nhe resonance_mvp_main route_table icons i18n changelog em_dash docs` 29/29.

SQLite tạm, engine giả, không gọi model.

## Còn để lại (Minor, chưa làm)

- Hai thành viên trùng tên hiển thị: @ luôn trỏ người đầu.
- Ngăn chưa bẫy focus và tab chưa đi bằng phím mũi tên.
- Cho thêm lượt nâng trần tối đa của mức 2 thì mức hiện thành "Tuỳ chỉnh".
- Mỗi sự kiện nhóm tải lại danh sách nhóm kèm tin cuối của từng nhóm (tối đa 50); chạy được, nên theo dõi.

## Kiểm

| Lệnh | Kết quả |
|---|---|
| `tests/python/test_team_ux.py` | cho thêm lượt theo trần ngày, trần mỗi việc, trần bước (thêm lại bước bị từ chối, chạy tới xong), Toàn quyền đặt số (nâng trong hộp quyền và trần tối đa), trần không còn chặn, trần dưới số đã dùng, việc không chặn thì không làm gì; số đã dùng hôm nay; lý do chặn và tin hệ thống bằng lời thường |
| `tests/python/test_team_api.py` | `last` ở danh sách, `usage` ở chi tiết, route extend: 200, 409, 404 |
| `tests/js/test_teams_ui.js` | 163 mục: danh sách, bong bóng, dải việc, nút giải quyết, chip việc, @ theo chữ (email, NFD, tên dài thắng), ba mức, tab Nhóm và Nâng cao, ngăn, khung soạn một hàng, CSS `[hidden]` và 44px, chữ vi/en |
| `tests/run.py --js` | 186/186 |
| Python chịu tác động | 12/12 (`team_runner`, `team_t1_review`, `team_authority`, `team_reactions`, `team_history`, `team_ux`, `team_perf_bench`, `route_table`, `team_store`, `team_api`, `icons`) |

**Smoke giao diện** (sandbox cổng 7788, nhóm tạm dừng, trần ngày 0, không gọi model). Ảnh trong `exports/reviews/team-t5-smoke/`; so với ảnh trước trong `exports/reviews/team-ux-audit/`.

| Đo | Trước | Sau |
|---|---|---|
| Khung tin trên điện thoại 375px | 284px | 461px |
| Hàng soạn | 209 tới 249px | 44px |
| Tab Nhóm (cài đặt) | 2.003px | 908px, số đặt trong Nâng cao |
| Tên thành viên | mỗi chữ một dòng | một dòng |
| Khung chat trên máy tính 1366px | 564px | 926px, ngăn đóng |

Đã thử trên sandbox:
- ba mức (đổi qua lại, mức 3 hỏi lại);
- Cho thêm lượt (trần ngày 0 thành 20, việc về hàng chờ; sau đó đặt lại 0 ngay);
- @ chèn tên và dòng gợi ý Giao việc;
- ngăn kéo với nền mờ ở 900px;
- tin hệ thống mới.

Chưa thử trên máy thật iOS/Android hay với trình đọc màn hình.
