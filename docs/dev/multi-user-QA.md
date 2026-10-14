# Khảo sát: hỗ trợ nhiều người dùng và phân quyền (RBAC / ABAC / ReBAC / PBAC)

> Khảo sát dev, viết 2026-10-14 trên `nguyenban`, nền v0.131.0. Mục tiêu: trả lời hai câu hỏi
> - **Có thêm được nhiều người dùng kèm phân quyền hay không?**
> - **Nếu được thì giữ FastAPI + SQLite hay chuyển Django & DRF + PostgreSQL?**
>
> Phương pháp: đọc trực tiếp mã nguồn (auth, session, lưu trữ, hai cổng quyền), không suy đoán.
> Mọi khẳng định dưới đây đều kèm `file:line` để tự kiểm lại. Đây là **khảo sát, chưa viết mã**.

---

## Kết luận ngắn

**1. Làm được, và không cần đổi framework.** Nhưng phải nói thẳng: Javis hiện **không có khái niệm "người dùng"** ở tầng nào cả. Đây là thêm một tầng mới, không phải bật một cờ.

**2. Giữ FastAPI + SQLite.** Django/DRF là câu trả lời sai cho bài toán này: nó thay tầng bạn **không thiếu** (ORM, serialization) và không cho tầng bạn **thiếu** (một điểm quyết định quyền). PostgreSQL là quyết định **độc lập**, không liên quan RBAC, và chỉ nên làm khi cần nhiều tiến trình ghi.

**3. Phát hiện quan trọng nhất:** một mô hình phân quyền đã chạy được và đã có test **đang nằm sẵn trong repo** tại `server/resonance_grants.py` (137 dòng). Việc phải làm là **tổng quát hoá nó**, không phải thiết kế mô hình mới.

---

## Phần 1. Hiện trạng mô hình danh tính

### 1.1 Chỉ có MỘT tài khoản, lưu dạng object đơn

`server/config.py:35`:

```python
"auth": {"username": "", "password_hash": "", "salt": "",
         "totp": {"enabled": False, "secret": "", "recovery": [], "last_step": 0}},
```

Một dict, một username, một hash. Không có bảng `users`, không có danh sách, không có id.

Bằng chứng bổ sung:
- `auth_enabled()` (`config.py:1036-1038`) chỉ là phép kiểm truthiness trên **một** hash.
- `/auth/setup` (`main.py:1375-1390`) từ chối khi **đã có tài khoản**, không phải khi "user này đã tồn tại".
- `/auth/password` **thay** hash duy nhất, không có tài khoản thứ hai để giữ.
- `grep password_hash` toàn repo chỉ ra các điểm một-bản-ghi này cộng với test.

**Hash:** PBKDF2-HMAC-SHA256, 120.000 vòng, salt 16 byte hex (`config.py:1030-1033`). Salt và hash lưu **cạnh nhau, dạng hex thô** trong cùng object. `password_hash` **không** nằm trong `_SECRET_PATHS` nên không được mã hoá khi lưu, khác với `auth.totp.secret` (`config.py:522`).

### 1.2 Session KHÔNG mang danh tính

`config.py:1268-1272`:

```python
def new_session():
    t = secrets.token_urlsafe(32)
    SESSIONS[t] = _time.time()
    _save_sessions()
    return t
```

Giá trị lưu theo token là **một số float**. `valid_session()` (`config.py:1275-1282`) chỉ trả lời "token này có tồn tại và dưới 30 ngày không". **Không có cách nào hỏi "session này là ai"**, vì câu trả lời luôn là "chủ máy".

Session nằm ở `<STATE_DIR>/.sessions.json`, **sống qua khởi động lại** (`SESSIONS = _load_sessions()` chạy lúc import, `config.py:1250`).

### 1.3 Lỗi đang tồn tại: `_save_sessions` ghi không khoá, không nguyên tử

`config.py:1252-1257`:

```python
def _save_sessions():
    try:
        _SESS_PATH.write_text(json.dumps(SESSIONS), encoding="utf-8")
```

Không lock, không file tạm, không `os.replace`. Ghi **toàn bộ** dict `SESSIONS` toàn cục. Mà `_load_sessions` (`config.py:1240-1248`) gặp JSON hỏng thì `except Exception: pass` rồi trả `{}`.

**Hệ quả:** JSON hỏng một lần là **đăng xuất tất cả mọi người**. Với một người dùng đây là phiền; với mười người dùng, mỗi lần một người đăng nhập là một lần ghi đè toàn bộ file bằng thao tác không nguyên tử.

Đây là lý do kỹ thuật tốt để chuyển session sang SQLite, và là việc **nên làm kể cả khi chưa multi-user**.

### 1.4 2FA theo INSTANCE, không theo user

Một secret TOTP cho cả máy, trong `auth.totp`. Toán ở `server/totp.py` (RFC 6238 thuần stdlib, HMAC-SHA1 / 30s / 6 số, cửa sổ ±1 bước), có **chống phát lại** tường minh (`totp.py:71-77`), secret **được mã hoá Fernet**, 10 mã khôi phục băm bằng cùng PBKDF2.

Vì chỉ có một tài khoản nên "2FA theo user" chưa có ý nghĩa, nhưng **cấu trúc lưu là một object đơn**, thêm user thì phải tách khối này ra và mã hoá riêng từng secret.

### 1.5 Mã thiết lập đã bị bỏ từ 0.64.47

`main.py:1381-1383` nhận tham số `setup_token` nhưng **bỏ qua**, để client cũ không lỗi. Chỉ còn `_SETUP_TOKEN_PATH` và `clear_setup_token()` gọi một lần lúc boot. **Đừng thiết kế dựa trên cơ chế này, nó không còn.**

---

## Phần 2. Hai cổng quyền, và chúng đã tốt hơn vẻ ngoài

Đây là phần **quan trọng nhất** của khảo sát, vì nó quyết định chi phí thêm RBAC.

### 2.1 Cổng HTTP: deny-by-default, chỉ 14 đường miễn

`main.py:282-305`, middleware `_auth_guard`:

```python
@app.middleware("http")
async def _auth_guard(request: Request, call_next):
    if cfgmod.gate_active():
        path = duong_dan_router(request)   # KHÔNG phải request.url.path
        client_host = request.client.host if request.client else ""
        public = (path in _AUTH_PUBLIC_EXACT
                  or any(path.startswith(p) for p in _AUTH_PUBLIC_PREFIX)
                  or (path in _AUTH_LOCAL_EXACT and client_host in ("127.0.0.1", "::1"))
                  or ((path.startswith("/ext/") or path.startswith("/.well-known/"))
                      and plugins_host.http_cong_khai(path, request.method)))
        if not public and not cfgmod.valid_session(request.cookies.get("javis_session", "")):
            if not _token_ok(request, path):
                return JSONResponse({"error": "unauthorized", ...}, status_code=401)
    return await call_next(request)
```

Danh sách miễn là một tập **ĐÓNG, nhỏ, kiểm được**:

```python
_AUTH_PUBLIC_PREFIX = ("/static", "/health", "/s/")                          # main.py:213
_AUTH_PUBLIC_EXACT  = ("/", "/favicon.ico", "/auth/status", "/auth/login",
                       "/auth/setup", "/brand-logo", "/brand-theme.css",
                       "/tls-check", "/hub/mcp", "/connect/oauth/callback",
                       "/whatsapp/webhook")                                   # main.py:215-223
_AUTH_LOCAL_EXACT   = ("/telegram/send-file", "/reminders",
                       "/reminders/cancel", "/reminders/update")              # main.py:233
```

**Đây là tin tốt lớn.** `main.py` có **277 decorator** (157 `@app.post` + 115 `@app.get` + 3 `@app.websocket` + 2 `@app.api_route`), cộng 7 module `server/routes/*.py` đăng ký qua `register(app, ...)`, tổng khoảng **350 handler**. Nhưng chỉ **14 đường miễn**.

Nghĩa là: **không phải rải kiểm tra quyền vào 350 route.** Chỉ cần đổi hàm quyết định **bên trong** `_auth_guard`. 277 decorator là **lý do phải dùng middleware**, không phải lý do phải viết lại.

Ghi chú an toàn đáng giữ: `duong_dan_router(request)` thay cho `request.url.path` đã vá một bypass thật (PYSEC-2026-161: header `Host: myhost.com/static` làm `request.url.path` đọc `/static/agents` trong khi router vẫn phục vụ `/agents`). Và `/s/` **có gạch chéo cuối là có chủ đích**: khai `/s` sẽ mở công khai luôn `/settings`, `/skills`, `/sessions`, `/stt`. Có test riêng canh đúng một ký tự này (`tests/python/test_share_link.py`).

### 2.2 Cổng tool: MỘT hàm, MỘT chỗ gọi

`server/mcp_catalog.py:506-544`:

```python
PERM_RANK = {"readonly": 0, "safe": 1, "full": 2}          # :29
_MODE_CAP = {"suggest": "readonly", "auto": "safe", "full": "full"}

def effective_perm(perm, mode):
    """Mức quyền HIỆU LỰC = chặt hơn giữa perm của connection và trần của mode."""
    perm = perm if perm in PERM_RANK else "full"
    cap = _MODE_CAP.get((mode or "full").strip().lower(), "full")
    return perm if PERM_RANK[perm] <= PERM_RANK[cap] else cap

def allowed(connector, perm, mode, tool, args=None):
    """(ok, lý_do_chặn_tiếng_Việt). Lớp CỨNG - không phụ thuộc prompt."""
```

Đã grep xác nhận: `mcp_catalog.allowed()` có **đúng một** chỗ gọi:

```
server/mcp_hub.py:220    ok, why = mcp_catalog.allowed(connector, conn.get("perm"), mode, tool, args)
```

`effective_perm()` có 3 chỗ, tất cả trên cùng một trục (`mcp_hub.py:616`, `mcp_hub.py:1100`, `plugins_host.py:810`).

### 2.3 Trục hiện tại là một DÀN, không phải một giá trị

Đây là chỗ cần đặt vấn đề lại cho đúng. Mô tả "mức quyền của TOOL, không phải của NGƯỜI" là đúng nhưng chưa chính xác về hệ quả.

Trục hiện tại là **"lượt chạy này được làm bao nhiêu"**, và nó đã được ghép bằng `min()` trên `PERM_RANK`. Đây là một **dàn (lattice)** thật:

```
mode (suggest/auto/full)  ──cap──┐
                                 ├──► min() ──► mức hiệu lực (readonly/safe/full)
perm của connection       ───────┘
```

**Hệ quả thiết kế:** danh tính là trục **TRỰC GIAO**, không phải trục **thay thế**. Thêm `principal` vào hàm `min()` đó là **mở rộng tự nhiên**, không phải đè một tầng mới lên.

Bốn chỗ khai báo lại `muc_quyen` độc lập (loops, reminders, chatbots, coding) trông như nợ kỹ thuật nhưng **có chủ đích**: comment ghi *"cùng bộ từ với loop để người dùng chỉ phải học một lần"*. **Đừng gộp chúng khi làm RBAC**, sẽ phá tính nhất quán từ vựng của giao diện.

---

## Phần 3. Mô hình quyền đã có sẵn: `resonance_grants.py`

**Đây là phát hiện quan trọng nhất của khảo sát.**

`server/resonance_grants.py`, **137 dòng**, `POLICY_VERSION = "grants.v1"`. Docstring đầu file:

```
- `narrow`: quyền con chỉ là PHẦN GIAO của quyền cha và cái con xin (mục 5.4).
            Xin trường lạ hay thiếu: phần giao rỗng.
- `allows`: một quyền có cho thao tác trên đúng đích không.
A4 KHÔNG cấp quyền từ lời chat (D1): không có hàm nào ở đây đọc lời người dùng.
```

Ba hàm cốt lõi:

```python
ACTIONS = ("read_deliverable", "submit", "publish")                    # :26

def narrow(parent, request):        # :80  quyền con = PHẦN GIAO của cha và cái con xin
    ...
    acts = [a for a in _set(want_actions) if a in ACTIONS and a in _set(parent.get("actions"))]
    ...
    "write_paths": [x for x in _set(request.get("write_paths")) if x in _set(parent.get("write_paths"))],
    "read_paths":  [x for x in _set(request.get("read_paths"))  if x in _set(parent.get("read_paths"))],

def allows(grant, action, key):     # :106  "Thiếu gì cũng là không"
    if not grant or action not in ACTIONS or not key:
        return False
    if action not in _set(grant.get("actions")):
        return False
    paths = _set(grant.get("read_paths") if action == "read_deliverable" else grant.get("write_paths"))
    return key in paths

def path_key(brain_root, rel):      # :33  từ chối '..', đường tuyệt đối, ký tự điều khiển, symlink thoát
```

**Đây là mô hình capability có THU HẸP DẦN (attenuation)**, khoá theo đường dẫn, deny-by-default, và có một nguyên tắc bảo mật viết thẳng trong docstring: **không cấp quyền từ lời chat**.

Được lưu trong DB kèm `granted_by`, `generation`, `grant_events`, `scope_requests`; gọi từ 4 chỗ trong `resonance_store.py:2314, 3641, 3682, 3762`.

**Kết luận:** không phải thiết kế mô hình mới. **Tổng quát hoá cái đang có và đang được test**, giữ nguyên `narrow()` và `allows()` vì chúng đã đúng.

---

## Phần 4. Mảnh còn thiếu, và chúng nhỏ hơn tưởng

### 4.1 "Ai" đã có một nửa

`server/resonance_store.py:371-379`:

```python
@dataclass(frozen=True)
class Principal:
    """Ai đang thao tác. `kind`: owner (người dùng qua auth của host) hoặc agent.
    Host tạo, model không tự khai."""
    kind: str
    id: str
    brain_id: str
```

Nhưng nó được dựng bằng **literal cứng**: `Principal("owner", "owner", root)` ở 7 chỗ trong `resonance_api.py` và `main.py:6840`. Class đã có, **không có đường nối từ session đăng nhập vào Principal**. Đó mới là việc phải làm.

### 4.2 "Cái gì" đã có khoá chuẩn hoá

`server/capability_registry.py:34-40`:

```python
def brain_scope(brain):
    """Hash scope để Registry không lưu raw path và không trộn capability giữa hai brain."""
    raw = str(Path(brain or "brain").resolve()).casefold()
    return "brain_" + _sha(raw)[:20]
```

Đây là **khoá tài nguyên chuẩn hoá cho brain**, dùng xuyên suốt `capability_registry` và `memory_index`. Hàm `can(principal, action, resource)` không phải dựng từ số 0.

### 4.3 `turn_context` mang danh tính tới mọi tool hook, nhưng KHÔNG có luật nào đọc

`server/turn_context.py:11-19`:

```python
{"kenh": str, "sender_id": str, "chat_type": "private" | "group", "chat_id": str,
 "la_chu": bool}
```

`sender_id` = id nền tảng của người gửi tin **này**. `la_chu` = True trên bề mặt của chủ máy.

Docstring của module ghi thẳng động cơ tồn tại:

> *"a plugin wrapping an MCP that answers each person with their OWN permissions (a shop app where every staff member sees only their own work) had no way to tell which person a chatbot turn was answering"*

Tức **nhu cầu ABAC đã được cảm nhận trong mã**, và giải pháp lúc đó là thêm `sender_id`. Mối nối đã đặt sẵn; chỉ thiếu luật đọc nó.

Cảnh báo cho người đọc mã: `role` trong frontmatter agent **là câu mô tả persona**, không phải vai trò quyền. Đừng nhầm khi thiết kế.

### 4.4 `OWNER_ONLY_TOOLS`: tiền lệ duy nhất của "loại principal đổi danh sách tool"

`server/mcp_hub.py:98-102`:

```python
# Tools that only the OWNER's own sessions may see (0.77.0). A dedicated bot at the "can write" /
# "full" level gets hub tools, and there the person steering the model is a stranger. ...
OWNER_ONLY_TOOLS = frozenset({"javis_reply_policy", "javis_reply_policy_tune"})
```

Đây là **chỗ DUY NHẤT** trong toàn repo mà loại principal đổi danh sách tool: một `frozenset` hai phần tử, lọc bằng cờ `for_bot`. Đây là tiền lệ để nhân bản thành bảng thật.

### 4.5 Phân quyền nền tảng kênh đã có, chỉ khác trục

`server/owner_channels.py:11-13`:

> *"Access is FAIL-CLOSED, like Zalo and unlike Telegram: an empty allow-list lets nobody in. A stranger who writes gets a 4-digit pairing code and lands in a queue the owner approves with one click."*

Lưu ý **Telegram thì fail-OPEN**: whitelist rỗng nghĩa là **không giới hạn ai** (`bot_gateway.py:87`). Đây là kiểm soát **gia nhập** (được nói chuyện không), không phải **uỷ quyền** (được làm gì). Một khi đã được nhận, mọi người có cùng toàn quyền.

---

## Phần 5. Chặn cứng: chọn brain KHÔNG bị giới hạn vào `BRAINS_DIR`

**Đây là vấn đề lớn nhất khi thêm người dùng thứ hai.**

`server/main.py:5811-5815`:

```python
def _brain_root(brain: str) -> str:
    if not brain or brain == "brain":
        return str(_default_brain_dir())
    b = brain[5:] if brain.startswith("path:") else brain
    return b if os.path.isdir(b) else str(_default_brain_dir())
```

Client gửi `brain = "path:/thư/mục/bất/kỳ"`, server nhận, và **chỉ kiểm "có phải thư mục tồn tại không"**.

`_safe_path` (`main.py:7220`) khoá đường dẫn **tương đối** trong brain root, nhưng **brain root do client chọn**. Docstring `_default_brain_dir` (`main.py:5799-5809`) xác nhận đây là tính năng có chủ đích: *"user vẫn chọn brain khác trong danh sách hoặc folder ngoài bất kỳ qua `path:<thư mục>`"*.

- Với **một** người dùng: tính năng hữu ích.
- Với **nhiều** người dùng: **thất bại cách ly hoàn toàn**. Người A gửi `path:` trỏ vào thư mục của người B, hoặc vào bất kỳ đâu trên đĩa.

**Sửa cái này là QUYẾT ĐỊNH THIẾT KẾ, không phải sửa bug.** Hai lựa chọn: bỏ `path:` khi có nhiều người dùng, hoặc biến nó thành tài nguyên có chủ (dùng `brain_scope` làm khoá). Phải chốt **trước** khi viết dòng RBAC nào.

---

## Phần 6. Tầng lưu trữ

### 6.1 Javis ĐÃ dùng SQLite, rất nhiều

Tiền đề "chưa có DB" là **sai**. Không có ORM, không có DBMS riêng, nhưng có tầng SQLite đáng kể:

- `import sqlite3` trong **12 module** `server/*.py`.
- **11+ database** riêng biệt, DDL viết tay, `PRAGMA journal_mode=WAL` gần như mọi store.
- Không có SQLAlchemy, psycopg, asyncpg, alembic, Redis, Mongo. `grep` trên `requirements.txt` cũng trắng.

Các DB trên đĩa (trong `STATE_DIR`, mặc định `server/`):

| Module | File | Nội dung |
|---|---|---|
| `sessions.py` | `conversations.db` | `sessions`, `messages`, `projects`, + FTS5 |
| `conversations.py` | `customer_conversations.sqlite3` | `channel_accounts`, `customers`, `conversations`, `messages` |
| `task_store.py` | `kanban.sqlite3` | `boards`, `tasks` |
| `context_runtime.py` | `runtime.db` | 10 bảng runtime + quota + lease |
| `capability_registry.py` | `capability_registry.db` | registry + FTS5 |
| `memory_index.py` | `memory_index.db` | chỉ mục trên markdown của brain |
| `resonance_store.py` | `resonance.sqlite3` | goals, grants, submissions |
| `usage_index.py`, `conversation_state.py`, `session_brain.py`, `workflow_runs.py`, `chatbot_reply_policy_store.py` | | |

### 6.2 Tầng JSON và JSONL

- **JSON blob, ghi nguyên tử** (`.tmp` + `os.replace`): `chatbot_store.py:257`, `plugins_host.py:178`, `config.py:606`, `inbox.py:86`, `mcp_store.py`.
- **JSON blob, ghi KHÔNG nguyên tử**: `config.py:1252` (`.sessions.json`) và `.api_tokens.json`. **Đây là chỗ yếu nhất.**
- **JSONL append-only**: `usage-events.jsonl`, `mcp_audit.jsonl` (xoay ở 1 MB), `auth_audit.jsonl`, `chatbot-logs/<bot_id>.jsonl`.
- **Markdown trong git**: chính là brain, qua `git_brain.py`.

### 6.3 Đồng thời: một tiến trình, nhiều thread

Server chạy **một** tiến trình uvicorn (`main.py:22138`, `reload=False`, **không** `--workers`). Rủi ro chính là thread-vs-thread.

Cơ chế đang dùng:
- `threading.Lock` / `RLock` quanh mọi chu trình load-modify-save (43 chỗ).
- SQLite `BEGIN IMMEDIATE` + `busy_timeout` cho giao dịch thật.
- **Jitter-retry** ở `sessions.py:478-504` (12 lần thử, `timeout=1.0` cố ý ngắn rồi tự thử lại) - khuôn mẫu cẩn thận nhất trong repo.
- `os.replace` nguyên tử cho JSON (28 chỗ).
- `asyncio.Lock` cho phía event loop.
- **`BrainLock`** (`git_brain.py:1271-1330`): khoá file cấp HĐH duy nhất trong repo, `fcntl.flock` / `msvcrt.locking`, khoá theo brain tại `<brain>/.javis-learn.lock`. Docstring nêu lý do: *"asyncio.Lock không bảo vệ được tiến trình khác"*.

**Đánh giá rủi ro:** SQLite thì **thấp** (WAL + busy_timeout + BEGIN IMMEDIATE + lock + retry là chồng đủ vững trong một tiến trình). JSON blob thì **trung bình** (rename nguyên tử chống file hỏng nhưng **không** chống mất cập nhật; đúng đắn phụ thuộc mọi caller nhớ giữ lock). `config.py` session thì **cao nhất** (xem 1.3).

### 6.4 SQL là dialect SQLite thuần, không phải SQL chuẩn

Rải khắp mã: `INSERT ... ON CONFLICT(...) DO UPDATE`, `CREATE VIRTUAL TABLE ... USING fts5`, `PRAGMA table_info`, `AUTOINCREMENT`, placeholder `?`. FTS5 dùng cho tìm kiếm (`sessions.py`, `capability_registry.py`).

**Hệ quả:** chuyển PostgreSQL **không** phải đổi connection string. Đây là một cuộc port thật, kèm viết lại toàn bộ tầng tìm kiếm FTS5.

### 6.5 Dữ liệu rất nhỏ

DB lớn nhất trên máy khảo sát là `usage_index.db` khoảng **484 KB**. `conversations.db` 168 KB (WAL 1.2 MB). Đây là các file nhỏ.

**PostgreSQL vì dung lượng là không có cơ sở.**

### 6.6 Nếp migrate đã có sẵn và nhất quán

Không có framework migration, nhưng có **bảy khuôn mẫu thủ công** đang chạy tốt:

1. `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` lúc mở (`sessions.py:412-455` thêm 9 cột theo thời gian).
2. Tuple `_MIGRATIONS` idempotent, bỏ qua lỗi "duplicate column" (`chatbot_reply_policy_store.py:84`).
3. Hằng `schema_version` trong bảng meta (`REGISTRY_SCHEMA_VERSION`, `INDEX_SCHEMA_VERSION`).
4. Hàm backfill có tên, chạy một lần (`resonance_store.py`, `_a2_migrate`, `_a4_migrate`).
5. Field `version` trong JSON + migrate-on-read kèm backup (`mcp_store.py:69-115`).
6. Cờ migrate trong config (`learn.py`, `_migrations` dict).
7. Cách ly file hỏng bằng đổi tên `*.corrupt-<timestamp>` (`capability_registry.py:186-192`).

Thêm bảng `users` và `grants` **đi theo đúng nếp này**, không cần dựng framework.

### 6.7 Bí mật đang mã hoá, nhưng là store phẳng

`secrets_store.py`: Fernet (AES128-CBC + HMAC), tiền tố `enc:`, khoá ở `STATE_DIR/.secret_key`. Mất khoá thì giải mã ra `""` và người dùng nhập lại, cố ý fail-safe.

**Với multi-user:** bí mật của mỗi người phải được **namespace**, nếu không thì người này đọc được token của người kia.

---

## Phần 7. Trả lời câu hỏi framework

### 7.1 Vì sao KHÔNG chuyển Django & DRF

| Vấn đề | Vì sao Django không giải quyết |
|---|---|
| Django ORM là model-centric | Javis là **file-centric**. `agent_assets.py` ghi rõ: *"KHÔNG nằm trong SQLite: một trợ lý là một file `.md` trong brain"*. Brain là cây markdown git-backed, ORM không map vào đó. |
| `django.contrib.auth` là RBAC cấp model | Không diễn tả được "brain A, agent B, tool C, mức full, trên kênh Zalo". |
| DRF cho serialization + viewset | Javis **không thiếu** hai thứ đó. Nó thiếu **điểm quyết định quyền**. |
| Async | FastAPI async + 3 WebSocket + SSE streaming token từ CLI engine. Django async yếu hơn ở đúng chỗ này. |
| Chi phí | **133.927 dòng** Python (`server/`) + **40.540 dòng** JS (`dashboard/`) + **673 file test**. Đây là viết lại, không phải migration. |

### 7.2 Vì sao SQLite đủ, và khi nào PostgreSQL

PostgreSQL là **quyết định độc lập**, không liên quan RBAC. Chỉ cần khi:
- Nhiều tiến trình ghi đồng thời (hiện tại: một tiến trình, có `--workers` không dùng).
- Vài trăm người dùng đồng thời.
- Cần tìm kiếm/khôi phục phức tạp hơn FTS5.

Với vài chục người dùng, **SQLite + WAL + một tiến trình là đủ**, và giữ được triết lý self-host một tiến trình. Nếu sau này cần, đường đi là thêm lớp repository rồi đổi backend, **đừng đổi vì RBAC**.

---

## Phần 8. Mô hình đề xuất

RBAC thuần **không đủ** (quyền phụ thuộc brain, tool, kênh, mức). ABAC thuần **không đủ** (thiếu quan hệ sở hữu). ReBAC là đúng nhất cho "chia sẻ brain cho người khác". PBAC là lớp bọc để viết luật.

Đề xuất **bốn tầng, dùng chung một điểm quyết định**:

1. **Principal** - mở rộng cái đã có ở `resonance_store.py:371`. Thêm `user_id` thật thay vì literal `"owner"`.
2. **Role (RBAC)** - `owner` / `editor` / `viewer` / `chatter`. Dùng cho giao diện và giá trị mặc định.
3. **Relation (ReBAC)** - bảng `grants(subject, relation, object)`: `user:7 → editor → brain:trolysoNguyenBan`. Đây là thứ cho phép chia sẻ brain.
4. **Policy (PBAC)** - hàm `can(principal, action, resource, context)` là **nơi DUY NHẤT** quyết định, tham chiếu cả role, relation, attribute (tool, `muc_quyen`, kênh, giờ). Viết bằng Python trong repo.

**Về thư viện:**
- **Casbin**: làm được RBAC + ABAC; ReBAC phải mô phỏng bằng role graph, hơi gượng.
- **OpenFGA** (Zanzibar): đúng ReBAC nhưng là service Go riêng, **phá triết lý self-host một tiến trình**.
- **Tự viết PEP**, tổng quát hoá `resonance_grants.py`: sạch hơn cả hai, vì luật của Javis rất đặc thù.

**Điểm mấu chốt:** chỉ có **một** hàm `can()`, gọi ở **hai** chỗ - `_auth_guard` (`main.py:282`) và `mcp_hub._guard` (`mcp_hub.py:212`). Không rải khắp 350 route.

---

## Phần 9. Lộ trình

Đã sắp lại sau ba vòng rà soát. Hai việc đầu nên làm **kể cả khi chưa multi-user**.

| # | Việc | Vì sao ở vị trí này |
|---|---|---|
| 0 | **Chốt chính sách `path:` trong `_brain_root`** (`main.py:5811`) | Là **quyết định thiết kế**, không phải code. Là thất bại cách ly nghiêm trọng nhất. Phải chốt trước khi viết RBAC. |
| 1 | **Sửa `_save_sessions`** (`config.py:1252`) và chuyển session sang SQLite | Lỗi đang tồn tại ở bản một người dùng: JSON hỏng thì đăng xuất hết mọi người. |
| 2 | **Thêm `user_id` vào bản ghi session** | Đổi `{token: ts}` thành `{token: {ts, user_id}}`. Thay đổi nhỏ nhất. |
| 3 | **Nối `session → Principal`** | Thay literal `Principal("owner","owner",brain)` ở 7 chỗ. |
| 4 | **Bảng `users`** + đăng nhập nhiều tài khoản + 2FA theo user | Tách khối `auth.totp` ra từng người, mã hoá riêng từng secret. |
| 5 | **Bảng `grants(subject, relation, object)`** | Tổng quát hoá `resonance_grants`, **giữ nguyên** `narrow()` và `allows()`. |
| 6 | **Thêm principal vào `effective_perm`** (`mcp_catalog.py:506`) | Mở rộng dàn `min()`, không đè tầng mới. |
| 7 | **PostgreSQL** (nếu thật sự cần) | Tách hẳn khỏi RBAC. Chỉ khi cần nhiều tiến trình ghi. |

---

## Phần 10. Bề mặt test phải viết lại

Khoảng **15 file** khẳng định hành vi một-admin:

| File | Ghim điều gì |
|---|---|
| `tests/python/test_security.py` | **Nặng nhất.** Ghim cả ma trận quyền MCP lẫn `_AUTH_LOCAL_EXACT` lẫn session TTL trên một map `SESSIONS`. |
| `tests/python/test_admin_env_password.py` | `JAVIS_ADMIN_PASSWORD` tạo/đặt lại **tài khoản** admin. |
| `tests/python/test_login_env_upgrade.py` | Marker env không được ghi đè mật khẩu/tên/2FA/session của **chủ**. |
| `tests/python/test_doi_mat_khau.py` | `/auth/password` đổi **tài khoản**; canary `/auth/setup` vẫn từ chối tài khoản thứ hai. |
| `tests/python/test_ma_thiet_lap.py` | Lần đầu tạo **admin** chỉ với tên + mật khẩu. |
| `tests/python/test_2fa_totp.py`, `test_2fa_mat_khoa_khong_im_lang.py` | Một secret TOTP duy nhất trong `cfg["auth"]["totp"]`. |
| `tests/python/test_host_header_khong_lach_dang_nhap.py` | Chính cổng `_auth_guard` (PYSEC-2026-161). |
| `tests/python/test_ws_origin_guard.py` | Cổng bắt tay WebSocket theo `gate_active()`. |
| `tests/python/test_login_forgiving.py`, `test_plugin_http.py`, `_live_route.py` | Đăng nhập bằng **một** tài khoản. |
| `tests/js/test_2fa_loi_vao.js`, `test_token_api_ui.js`, `test_login_screen_opaque.js` | Màn đăng nhập / tài khoản / token. |

Thêm khoảng **10 file** gắn với mô hình `muc_quyen`, sẽ đổi nếu `muc_quyen` nhận thêm principal:
`test_nhac_hen_muc_quyen.py`, `test_chatbot_muc_quyen.py`, `test_full_quyen_ungated.py`, `test_chatbot_cach_ly.py`, `test_run_command_quyen.py`, `test_engine_ngang_quyen.py`, `test_muc_mac_dinh.py`, `test_bot_toan_quyen_khong_lo.py`, `tests/js/test_task_suggest.js`, `tests/js/test_trang_chatbot.js`.

**Một test đáng chú ý:** `tests/python/test_chatbot_cach_ly.py` khẳng định *"Rào DUY NHẤT của bot chuyên trách: nó chỉ thấy brain của chính nó"*. Đây là mô hình cách ly **đúng nhất** đang có trong repo (`mcp_hub.py:1478-1482`: thiếu header `X-Javis-Vault` hợp lệ thì `vault_root = None`, không có tool), và nên là **khuôn mẫu cho cách ly giữa người dùng**.

---

## Phần 11. Phụ lục: điểm neo `file:line`

### Danh tính và đăng nhập
| Việc | Chỗ |
|---|---|
| Khối `auth` duy nhất | `server/config.py:35` |
| `hash_password` PBKDF2 120k | `server/config.py:1030-1033` |
| `verify_password`, `username_matches` | `server/config.py:1073-1093` |
| `require_login` fail-closed theo bind | `server/config.py:1041-1054` |
| `gate_active` | `server/config.py:1057-1059` |
| `new_session` (không mang danh tính) | `server/config.py:1268-1272` |
| `valid_session` | `server/config.py:1275-1282` |
| `_save_sessions` **ghi không khoá, không nguyên tử** | `server/config.py:1252-1257` |
| `provision_admin_from_env` | `server/config.py:1445-1510` |
| `auth.totp.secret` trong `_SECRET_PATHS` | `server/config.py:522` |
| `POST /auth/login`, `/auth/setup` | `server/main.py:1413`, `1375` |
| Mã thiết lập đã bỏ (0.64.47) | `server/main.py:1381-1383` |
| Token API, scope `full`/`chat` | `server/config.py:1305-1399` |

### Cổng quyền
| Việc | Chỗ |
|---|---|
| `_AUTH_PUBLIC_PREFIX` / `_EXACT` / `_LOCAL_EXACT` | `server/main.py:213`, `215-223`, `233` |
| `duong_dan_router` (vá PYSEC-2026-161) | `server/main.py:236-252` |
| `_csrf_guard` | `server/main.py:255-279` |
| `_auth_guard` (deny-by-default) | `server/main.py:282-305` |
| `WebSocketGuard` | `server/main.py:378`, `server/web_security.py:204-224` |
| `PERM_RANK` / `_MODE_CAP` | `server/mcp_catalog.py:29-30` |
| `effective_perm` (dàn `min()`) | `server/mcp_catalog.py:506-510` |
| `allowed` (lớp cứng) | `server/mcp_catalog.py:513-544` |
| **Chỗ gọi DUY NHẤT** | `server/mcp_hub.py:220` |
| `_guard` | `server/mcp_hub.py:212-248` |
| `OWNER_ONLY_TOOLS` | `server/mcp_hub.py:98-102` |
| `VALID_MIN_MODE` / `_min_mode_ok` | `server/plugins_host.py:55`, `809-812` |
| `ActorPolicy` / `_hard_filter` | `server/capability_resolver.py:52-58`, `69-89` |
| `_permission_gate` (engine CLI) | `server/claude_sdk_engine.py:562-586` |

### Đã có sẵn để tái dùng
| Việc | Chỗ |
|---|---|
| **Luật quyền thuần (`narrow`, `allows`, `path_key`)** | `server/resonance_grants.py` (137 dòng) |
| `Principal` | `server/resonance_store.py:371-379` |
| `_owner_only` | `server/resonance_store.py:473-476` |
| `brain_scope` (khoá tài nguyên chuẩn hoá) | `server/capability_registry.py:34-40` |
| `turn` dict (`sender_id`, `la_chu`) | `server/turn_context.py:11-19` |
| Ghi chú `owner_chat` / `chat_id` | `server/task_store.py:98-99`, `server/self_improve.py:317` |
| `"mine"` (nhóm giao diện, KHÔNG phải lọc truy cập) | `server/background_status.py:160` |

### Rủi ro và chặn cứng
| Việc | Chỗ |
|---|---|
| **`_brain_root` không giới hạn vào `BRAINS_DIR`** | `server/main.py:5811-5815` |
| `_default_brain_dir` (xác nhận `path:` là chủ đích) | `server/main.py:5799-5809` |
| `_safe_path` / `_safe_serve_path` (chỉ khoá đường TƯƠNG ĐỐI) | `server/main.py:7220`, `7230` |
| `/s/<token>` (chỉ token, không auth) | `server/share_store.py:63-91` |
| `read_docs` từ chối thiếu header vault | `server/mcp_hub.py:1478-1482` |
| Kênh chủ: fail-closed (Slack/Zalo) vs fail-OPEN (Telegram) | `server/owner_channels.py:11-13`, `bot_gateway.py:87` |

---

## Tóm tắt một khối

1. **Một người dùng duy nhất, thật sự.** `cfg["auth"]` là một dict; scope duy nhất của một credential là token API hai giá trị trên allowlist bốn đường.
2. **Hai cổng quyền, và cả hai đã tốt hơn vẻ ngoài.** HTTP: deny-by-default, 14 đường miễn trên ~350 handler. Tool: một hàm `allowed()`, một chỗ gọi, một dàn `min()`.
3. **Mô hình quyền đúng đã có sẵn** ở `resonance_grants.py`: capability thu hẹp dần, deny-by-default, khoá theo đường dẫn, không đọc lời chat. Việc phải làm là tổng quát hoá, không phải phát minh.
4. **"Ai" và "cái gì" đều đã có một nửa**: `Principal` đã có class nhưng dựng bằng literal; `brain_scope` đã có khoá chuẩn hoá; `turn_context` đã mang `sender_id` tới mọi hook nhưng không luật nào đọc.
5. **Chặn cứng là `_brain_root`**, không phải thiếu RBAC. Phải chốt chính sách `path:` trước.
6. **`_save_sessions` là lỗi đang tồn tại**, nên sửa kể cả khi chưa multi-user.
7. **Giữ FastAPI + SQLite.** Django/DRF thay tầng không thiếu và không cho tầng đang thiếu. PostgreSQL tách riêng.
