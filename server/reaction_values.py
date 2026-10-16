"""Giá trị và kiểm tra đầu vào của sáu reaction, dùng chung cho chat riêng và chat nhóm (T2, 0.154.0).

Hợp đồng: docs/dev/team-chat-t2-contract.md mục 1-3. Mã emoji giữ đúng mã đã dùng ở chat nhóm từ 0.120.0 (`love`, không
phải `heart`) để dữ liệu cũ còn đọc được. Không import gì ngoài thư viện chuẩn: `sessions.py` (stdlib-only) dùng được.
"""
from __future__ import annotations

from typing import Any, Optional

REACTIONS = ("like", "love", "haha", "wow", "sad", "angry")
REACTION_EMOJI = {"like": "👍", "love": "❤️", "haha": "😂", "wow": "😮", "sad": "😢", "angry": "😡"}
REASONS = ("on_point", "wrong_facts", "missing", "too_long", "unclear", "off_topic")
AXES = ("", "should_speak", "should_silent")
SOURCE_KINDS = ("owner", "leader", "member")
STATUSES = ("received", "pending_review", "reviewing", "adjusted", "no_change", "waiting_quota", "stored_only")
COMMENT_MAX = 500
NONCE_MAX = 100
# Nhãn cho khối phản hồi trong lời nhắc của agent (gửi model, không dịch).
REASON_LABEL = {"on_point": "Đúng ý", "wrong_facts": "Sai dữ kiện", "missing": "Thiếu ý", "too_long": "Quá dài",
                "unclear": "Khó hiểu", "off_topic": "Lạc đề"}
AXIS_LABEL = {"should_speak": "Đáng lẽ nên nói", "should_silent": "Nên im (chen ngang)"}
# Trường của khuôn reaction `R` trả cho giao diện (hợp đồng mục 3).
PUBLIC_FIELDS = ("value", "reasons", "axis", "comment", "stale", "status", "status_reason", "source_kind", "revision",
                 "updated_at")


class ReactionInputError(ValueError):
    """Thân request sai (giá trị lạ, quá dài, nonce rỗng hay quá dài): route trả 400. `en`: bản tiếng Anh của câu lỗi
    (route hiện theo ngôn ngữ giao diện qua localefmt)."""

    def __init__(self, vi: str, en: str = ""):
        super().__init__(vi)
        self.vi = vi
        self.en = en or vi


def normalize(value: Any, reasons: Any, axis: Any, comment: Any, nonce: Any) -> dict:
    """Kiểm và chuẩn hoá một thao tác reaction. Ném `ReactionInputError` khi sai. `value` None/""/"none" là bỏ emoji."""
    val = None if value in (None, "", "none") else str(value)
    if val is not None and val not in REACTIONS:
        raise ReactionInputError("reaction không hợp lệ", "invalid reaction")
    if reasons is not None and not isinstance(reasons, (list, tuple)):
        raise ReactionInputError("lý do phải là danh sách", "reasons must be a list")
    rs = list(dict.fromkeys(str(x) for x in (reasons or [])))
    if any(x not in REASONS for x in rs):
        raise ReactionInputError("lý do không hợp lệ", "invalid reason")
    ax = str(axis or "").strip()
    if ax not in AXES:
        raise ReactionInputError("trục nói/im không hợp lệ", "invalid speak/silent axis")
    cm = str(comment or "").strip()
    if len(cm) > COMMENT_MAX:
        raise ReactionInputError(f"góp ý tối đa {COMMENT_MAX} ký tự", f"comment is at most {COMMENT_MAX} characters")
    nc = str(nonce or "").strip()
    if not nc or len(nc) > NONCE_MAX:
        raise ReactionInputError("nonce không hợp lệ", "invalid nonce")
    return {"value": val, "reasons": rs, "axis": ax, "comment": cm, "nonce": nc}


def is_empty(state: dict) -> bool:
    """Chủ đã bỏ hết (không emoji, không lý do, không trục, không góp ý): reaction là `null`."""
    return not state.get("value") and not state.get("reasons") and not state.get("axis") and not state.get("comment")


def public(state: Optional[dict]) -> Optional[dict]:
    """Khuôn `R` của hợp đồng (mục 3) từ một dòng reaction đã đọc; None khi không có."""
    if not state:
        return None
    out = {k: state.get(k) for k in PUBLIC_FIELDS}
    out["reasons"] = list(out.get("reasons") or [])
    out["axis"] = out.get("axis") or ""
    out["comment"] = out.get("comment") or ""
    out["stale"] = bool(out.get("stale"))
    out["status"] = out.get("status") or "received"
    out["status_reason"] = out.get("status_reason") or ""
    out["source_kind"] = out.get("source_kind") or "owner"
    out["revision"] = int(out.get("revision") or 0)
    return out
