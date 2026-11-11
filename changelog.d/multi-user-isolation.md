---
bump: minor
title: Every account's engines reach the MCP hub as that account
---
## vi
### Bảo mật
- **Hub MCP nhận đúng tài khoản đang gọi.** Mỗi tài khoản có hub token riêng. Claude Code, Codex, Grok và Antigravity của một member chạy tool dưới tên member đó, không còn ghi vào việc và lịch của tài khoản chính. Hub từ chối brain thuộc tài khoản khác. Token máy cũ vẫn dùng được và thuộc tài khoản chính.
- **Mỗi tài khoản có kết nối MCP riêng.** Kết nối, token OAuth, nhật ký gọi tool và trạng thái sức khoẻ của member tách khỏi tài khoản chính. Admin có thể chia sẻ một kết nối của mình cho member với mức quyền giới hạn (chỉ đọc, an toàn hoặc đầy đủ) và thu hồi bất cứ lúc nào. Member dùng được kết nối được chia sẻ nhưng không sửa, tắt, xoá hay đăng nhập lại nó.
- **Mỗi tài khoản có kênh Telegram, Zalo, Slack, WhatsApp, Discord và Lark riêng.** Member lưu token bot của mình thì chỉ bot của member bật lại, bot của admin vẫn chạy. Mỗi lượt chạy dưới tên tài khoản sở hữu bot, dùng brain của tài khoản đó. Phiên chat, brain đã chọn, agent đã ghim và hàng chờ ghép nối tách theo tài khoản. Xoá một tài khoản thì bot của nó dừng.

## en
### Security
- **The MCP hub knows which account is calling.** Every account has its own hub token. A member's Claude Code, Codex, Grok and Antigravity run tools as that member and no longer write into the primary account's tasks and schedules. The hub refuses a brain that belongs to another account. The old machine token still works and belongs to the primary account.
- **Each account has its own MCP connections.** A member's connections, OAuth tokens, tool-call log and health status are kept apart from the primary account. An admin can share one of their own connections with a member at a capped permission (read-only, safe or full) and revoke it at any time. The member can use a shared connection but cannot change, disable, delete or sign it in again.
- **Each account has its own Telegram, Zalo, Slack, WhatsApp, Discord and Lark channels.** When a member saves their own bot token, only their bot restarts and the admin's bot keeps running. Every turn runs as the account that owns the bot, on that account's brains. Chat sessions, the chosen brain, pinned agents and the pairing queue are kept per account. Deleting an account stops its bots.
