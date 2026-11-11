# Nguồn: obra/superpowers

Bộ skill trong các thư mục anh em của file này (`.claude/skills/<slug>/`) lấy từ
[obra/superpowers](https://github.com/obra/superpowers) và **có sửa frontmatter** cho hợp quy
ước của repo này (xem mục dưới). Phần thân skill (quy trình, checklist, references, scripts)
giữ nguyên văn.

- Bản gốc: `v7.0.2`, commit `22f0b463e7a5e579904dd6211a520321035526f7` (2026-11-03)
- Giấy phép: MIT, nguyên văn ở `LICENSE` cạnh file này. Tác giả: Jesse Vincent.
- 15 skill: brainstorming, diagnosing-superpowers, dispatching-parallel-agents, executing-plans,
  finishing-a-development-branch, receiving-code-review, requesting-code-review,
  subagent-driven-development, systematic-debugging, test-driven-development, using-git-worktrees,
  using-superpowers, verification-before-completion, writing-plans, writing-skills.

## Đã sửa gì so với bản gốc

Thư mục này là **tầng hệ thống**: `system_sync.system_skill_slugs()` đọc nó, ship vào mọi brain,
và `tests/python/test_skill_caps.py` + `test_skill_da_ngon_ngu.py` lint mọi skill ở đây. Bản gốc
vi phạm hai luật của repo nên đã sửa đúng hai chỗ đó, không đụng gì khác:

1. **`description` không quá 150 ký tự** (`SKILL_DESC_MAX`). Bản gốc có 7 skill vượt trần (tới
   375 ký tự). Phần dư sau 150 bị router cắt im lặng, nên đã rút gọn phần mở đầu; ví dụ trigger
   vẫn nằm đầy đủ trong thân skill ở mục "When to use".
2. **Thêm `description_en`** cho cả 15 skill. Repo bắt buộc skill hệ thống có bản tiếng Anh,
   cũng chịu đúng trần 150, để danh sách skill không bị lẫn tiếng Việt khi chạy tiếng Anh.

Mười skill trong bộ này trùng tên với bản đã có trong pack `javis.superpowers` (bản port cũ).
Bản ở đây là bản mới hơn (7.0.2) và là bản repo thấy khi chạy Claude Code trong repo.

## Cập nhật lên bản mới

```
git clone --depth 1 https://github.com/obra/superpowers.git /tmp/sp
# chép 15 thư mục từ /tmp/sp/skills/ đè lên .claude/skills/<slug>/
# rồi áp lại đúng hai sửa đổi ở trên, cập nhật số bản + commit trong file này
```

## Muốn bản plugin đầy đủ

Repo gốc còn đóng gói hooks (`SessionStart` tự nạp `using-superpowers`) và marketplace. Bản ở
đây chỉ lấy `skills/`: Claude Code nhận diện skill qua `.claude/skills/` và nạp theo yêu cầu qua
tool Skill, nên không cần hook. Nếu muốn hook tự nạp mỗi phiên thì cài bằng plugin marketplace
thay vì chép tay.
