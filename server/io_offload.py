"""Đẩy I/O chặn (SQLite, file) ra luồng phụ để event loop không bao giờ bị giữ (review T1 R5, GOAL mục 10).

`run_io` vốn nằm ở `team_runner` (T1). Tách ra đây (T3 hiệu năng) để bộ phán xử (`agent_judge`) và nhịp lớp 3 dùng chung
mà không import chéo `team_runner`; `team_runner.run_io` vẫn là cùng hàm này.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
from typing import Any, Callable, Optional


async def run_io(fn: Callable, *args, undo: Optional[Callable[[Any], Any]] = None, **kwargs) -> Any:
    """Chạy một đơn vị I/O chặn (SQLite, file) ở luồng phụ, không chặn event loop (review T1, R5).

    Bị huỷ (kể cả huỷ lặp) khi luồng phụ còn đang ghi: KHÔNG bỏ đi giữa chừng. Chờ luồng phụ xong hẳn, chạy `undo(kết
    quả)` nếu có (vd. hoàn lượt vừa giữ chỗ, nhả khoá vừa lấy), rồi mới ném CancelledError. Nhờ vậy không có ghi dở,
    không nhả khoá trước khi việc ghi kết thúc, không có lượt giữ chỗ mồ côi."""
    fut = asyncio.ensure_future(asyncio.to_thread(fn, *args, **kwargs))
    cancelled = False
    while True:
        try:
            res = await asyncio.shield(fut)
            break
        except asyncio.CancelledError:
            if fut.cancelled():
                # Chính tác vụ luồng phụ đã bị huỷ (event loop đang đóng huỷ MỌI tác vụ): không còn gì để chờ. Trước đây
                # vòng lặp này quay mãi ở đó (shield một tương lai đã huỷ ném lại ngay).
                raise
            if fut.done() and not fut.cancelled():
                try:
                    res = fut.result()
                except BaseException:  # noqa: BLE001
                    raise asyncio.CancelledError()
                cancelled = True
                break
            cancelled = True
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError()
            raise
    if cancelled:
        if undo is not None:
            try:
                await run_io(undo, res)
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
        raise asyncio.CancelledError()
    return res


async def run_io_quiet(fn: Callable, *args, **kwargs) -> Any:
    """`run_io` cho bước dọn dẹp ở nhánh huỷ hay lỗi (hoàn lượt, chốt lượt): huỷ thêm lần nữa vẫn chờ ghi xong rồi nuốt
    CancelledError để người gọi tự ném lại đúng lỗi gốc. Trả None khi bị huỷ."""
    try:
        return await run_io(fn, *args, **kwargs)
    except asyncio.CancelledError:
        return None


async def call_hook(fn: Callable, *args, **kwargs) -> Any:
    """Gọi hàm móc của host (người gửi, bộ xử lý) mà không chặn event loop: coroutine function chạy trên loop; hàm
    thường (thường làm I/O) chạy ở luồng phụ. Hàm thường trả coroutine thì coroutine đó được chờ trên loop."""
    if inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(getattr(fn, "__call__", None)):
        return await fn(*args, **kwargs)
    got = await run_io(fn, *args, **kwargs)
    if inspect.isawaitable(got):
        got = await got
    return got


class LoopMarker:
    """Ghi một mốc (vd. `ledger.mark_engine`) từ hàm callback ĐỒNG BỘ do engine gọi ngay trước lời gọi model, mà không chặn
    event loop: trên luồng của loop thì đẩy việc ghi ra luồng phụ và giữ tương lai; ở luồng khác thì ghi luôn. Người gọi
    `await marker.wait()` trước khi chốt dòng sổ để thứ tự ghi (mốc rồi chốt) không đổi."""

    def __init__(self, write: Callable[[], Any]):
        self._write = write
        self.marked = False
        self._fut: Optional[asyncio.Future] = None
        try:
            self._loop = asyncio.get_running_loop()
            self._loop_thread = threading.get_ident()
        except RuntimeError:
            self._loop, self._loop_thread = None, None

    def __call__(self) -> None:
        if self.marked:
            return
        self.marked = True
        if self._loop is not None and threading.get_ident() == self._loop_thread:
            self._fut = self._loop.run_in_executor(None, self._write)
        else:
            self._write()

    async def wait(self) -> None:
        fut = self._fut
        if fut is None:
            return
        try:
            await asyncio.shield(fut)
        except asyncio.CancelledError:
            # Bị huỷ khi mốc đang ghi: chờ ghi xong (không bỏ dở) rồi ném lại huỷ.
            while not fut.done():
                try:
                    await asyncio.shield(fut)
                except asyncio.CancelledError:
                    continue
                except Exception:  # noqa: BLE001
                    break
            raise
        except Exception:  # noqa: BLE001 - ghi mốc lỗi không chặn việc chốt dòng sổ
            pass
