---
bump: minor
title: An admin can share one brain with a member, as editor or viewer
---
## vi
### Thêm mới
- **Chia sẻ một brain cho member.** Admin mời một member vào một brain của mình với vai trò editor (đọc và ghi) hoặc viewer (chỉ đọc). Lời mời nằm chờ, brain chỉ hiện ra khi member chấp nhận. Admin đổi vai trò hay thu hồi bất cứ lúc nào, và member mất quyền ngay ở yêu cầu kế tiếp. Member không chia sẻ tiếp được brain người khác chia cho mình, và vẫn không mở được brain khác của admin.
- **Viewer chỉ đọc thật sự.** Mọi thao tác ghi file trên brain đó trả 403. Lượt chat chạy ở mức `suggest` trên mọi engine, Claude bị tắt Write/Edit/Bash, Codex chạy sandbox `read-only`.
## en
### Added
- **Share one brain with a member.** An admin invites a member to one of their brains as editor (read and write) or viewer (read only). The invite waits, and the brain only appears once the member accepts. The admin can change the role or revoke at any time, and the member loses access on the next request. A member cannot pass a shared brain on, and still cannot open the admin's other brains.
- **A viewer really is read-only.** Every file write on that brain returns 403. Chat turns run at `suggest` on every engine, Claude has Write/Edit/Bash turned off, and Codex runs in its `read-only` sandbox.
