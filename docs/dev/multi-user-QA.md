# Khảo sát: hỗ trợ nhiều người dùng và phân quyền (RBAC / ABAC / ReBAC / PBAC)

> Khảo sát dev, viết 2026-10-14 trên `nguyenban`, nền v0.131.0. Mục tiêu: trả lời hai câu hỏi
> - **Có thêm được nhiều người dùng kèm phân quyền hay không?**
> - **Nếu được thì giữ FastAPI + SQLite hay chuyển Django & DRF + PostgreSQL?**
>
> Phương pháp: đọc trực tiếp mã nguồn (auth, session, lưu trữ, hai cổng quyền), không suy đoán.
> Mọi khẳng định dưới đây đều kèm `file:line` để tự kiểm lại. Đây là **khảo sát, chưa viết mã**.
>
> **Cập nhật 2026-10-15 (v0.153.0, commit `ce101967`).** Khảo sát lại bằng `git diff` từ đúng nền
> `cf315765` (0.131.0) tới HEAD, **không đọc lại toàn bộ mã nguồn**. Kết quả: ba kết luận của khảo
> sát **vẫn đúng**, nhưng con số và đường dẫn đã cũ, và có **bốn phát hiện mới** đáng kể. Toàn bộ nằm
> ở **Phần 12**; những chỗ đã sửa tại chỗ trong thân bài đều đánh dấu *(0.153.0)*.
>
> Phạm vi diff: 48 commit, 173 file, +29.203 dòng, -921 dòng. Kiểm bằng máy rằng **các file lõi quyền
> không đổi một ký tự** (danh sách ở Phần 12.1), nên phần lớn khảo sát gốc còn nguyên giá trị.
> Số dòng ở thân bài là số của **bản 0.131.0**; số của 0.153.0 nằm cạnh trong ngoặc.

---

## Kết luận ngắn

**1. Làm được, và không cần đổi framework.** Nhưng phải nói thẳng: Javis hiện **không có khái niệm "người dùng"** ở tầng nào cả. Đây là thêm một tầng mới, không phải bật một cờ.

**2. Giữ FastAPI + SQLite.** Django/DRF là câu trả lời sai cho bài toán này: nó thay tầng bạn **không thiếu** (ORM, serialization) và không cho tầng bạn **thiếu** (một điểm quyết định quyền). PostgreSQL là quyết định **độc lập**, không liên quan RBAC, và chỉ nên làm khi cần nhiều tiến trình ghi.

**3. Phát hiện quan trọng nhất:** một mô hình phân quyền đã chạy được và đã có test **đang nằm sẵn trong repo** tại `server/resonance_grants.py` (137 dòng). Việc phải làm là **tổng quát hoá nó**, không phải thiết kế mô hình mới.

---

## Phần 1. Hiện trạng mô hình danh tính

### 1.1 Chỉ có MỘT tài khoản, lưu dạng object đơn

`server/config.py:35` (dòng này **không đổi** ở 0.153.0):

```python
"auth": {"username": "", "password_hash": "", "salt": "",
         "totp": {"enabled": False, "secret": "", "recovery": [], "last_step": 0}},
```

Một dict, một username, một hash. Không có bảng `users`, không có danh sách, không có id.

Bằng chứng bổ sung:
- `auth_enabled()` (`config.py:1040-1044`) chỉ là phép kiểm truthiness trên **một** hash.
- `/auth/setup` (`main.py:1380-1395`) từ chối khi **đã có tài khoản**, không phải khi "user này đã tồn tại".
- `/auth/password` (`main.py:1604`) **thay** hash duy nhất, không có tài khoản thứ hai để giữ.
- `grep password_hash` toàn repo chỉ ra 13 chỗ, tất cả đều là các điểm một-bản-ghi này cộng với test.

**Hash:** PBKDF2-HMAC-SHA256, 120.000 vòng, salt 16 byte hex (`config.py:1034-1039`). Salt và hash lưu **cạnh nhau, dạng hex thô** trong cùng object. `password_hash` **không** nằm trong `_SECRET_PATHS` (`config.py:513-530`) nên không được mã hoá khi lưu, khác với `auth.totp.secret` (`config.py:528`).

### 1.2 Session KHÔNG mang danh tính

`config.py:1272-1278` (không đổi ở 0.153.0):

```python
def new_session():
    t = secrets.token_urlsafe(32)
    SESSIONS[t] = _time.time()
    _save_sessions()
    return t
```

Giá trị lưu theo token là **một số float**. `valid_session()` (`config.py:1279-1288`) chỉ trả lời "token này có tồn tại và dưới 30 ngày không". **Không có cách nào hỏi "session này là ai"**, vì câu trả lời luôn là "chủ máy".

Session nằm ở `<STATE_DIR>/.sessions.json`, **sống qua khởi động lại** (`SESSIONS = _load_sessions()` chạy lúc import, `config.py:1257`).

### 1.3 Lỗi đang tồn tại: `_save_sessions` ghi không khoá, không nguyên tử

`config.py:1260-1271` (không đổi ở 0.153.0):

```python
def _save_sessions():
    try:
        _SESS_PATH.write_text(json.dumps(SESSIONS), encoding="utf-8")
```

Không lock, không file tạm, không `os.replace`. Ghi **toàn bộ** dict `SESSIONS` toàn cục. Mà `_load_sessions` (`config.py:1244-1259`) gặp JSON hỏng thì `except Exception: pass` rồi trả `{}`.

**Hệ quả:** JSON hỏng một lần là **đăng xuất tất cả mọi người**. Với một người dùng đây là phiền; với mười người dùng, mỗi lần một người đăng nhập là một lần ghi đè toàn bộ file bằng thao tác không nguyên tử.

Đây là lý do kỹ thuật tốt để chuyển session sang SQLite, và là việc **nên làm kể cả khi chưa multi-user**.

### 1.4 2FA theo INSTANCE, không theo user

Một secret TOTP cho cả máy, trong `auth.totp`. Toán ở `server/totp.py` (RFC 6238 thuần stdlib, HMAC-SHA1 / 30s / 6 số, cửa sổ ±1 bước), có **chống phát lại** tường minh (`totp.py:71-77`), secret **được mã hoá Fernet**, 10 mã khôi phục băm bằng cùng PBKDF2.

Vì chỉ có một tài khoản nên "2FA theo user" chưa có ý nghĩa, nhưng **cấu trúc lưu là một object đơn**, thêm user thì phải tách khối này ra và mã hoá riêng từng secret.

### 1.5 Mã thiết lập đã bị bỏ từ 0.64.47

`main.py:1382-1388` nhận tham số `setup_token` nhưng **bỏ qua**, để client cũ không lỗi. Chỉ còn `_SETUP_TOKEN_PATH` và `clear_setup_token()` gọi một lần lúc boot (`main.py:12050-12052`). **Đừng thiết kế dựa trên cơ chế này, nó không còn.**

---

## Phần 2. Hai cổng quyền, và chúng đã tốt hơn vẻ ngoài

Đây là phần **quan trọng nhất** của khảo sát, vì nó quyết định chi phí thêm RBAC.

### 2.1 Cổng HTTP: deny-by-default, chỉ 15 đường miễn trên 428 handler

`main.py:288-312`, middleware `_auth_guard` (**không đổi một ký tự** ở 0.153.0):

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
_AUTH_PUBLIC_PREFIX = ("/static", "/health", "/s/")                          # main.py:215
_AUTH_PUBLIC_EXACT  = ("/", "/favicon.ico", "/auth/status", "/auth/login",
                       "/auth/setup", "/brand-logo", "/brand-theme.css",
                       "/tls-check", "/hub/mcp", "/connect/oauth/callback",
                       "/whatsapp/webhook",
                       "/messenger/webhook")                                 # main.py:217-228
_AUTH_LOCAL_EXACT   = ("/telegram/send-file", "/reminders",
                       "/reminders/cancel", "/reminders/update")              # main.py:238
```

**Đây là tin tốt lớn.** `main.py` có **285 decorator** (159 `@app.post` + 121 `@app.get` + 3 `@app.websocket` + 2 `@app.api_route`), cộng **13 module** đăng ký qua `register(app, ...)` hoặc `app.include_router`. Đếm bằng máy trên cả `server/`: **428 handler** (0.131.0: 394). Nhưng chỉ **15 đường miễn**.

Nghĩa là: **không phải rải kiểm tra quyền vào 428 route.** Chỉ cần đổi hàm quyết định **bên trong** `_auth_guard`. 285 decorator là **lý do phải dùng middleware**, không phải lý do phải viết lại.

**Con số này đã tăng, và tăng đúng chỗ đáng lo** *(0.153.0)*. So với 0.131.0: 394 → 428 handler, trong đó `main.py` 277 → 285 và **thêm một file route mới `server/team_api.py` với 26 handler** (17 `POST` + 7 `GET` + 1 `PATCH` + 1 `DELETE`). Đây là module route lớn nhất từng được thêm kể từ đợt chẻ `main.py`, và nó gần như toàn quyền ghi: tạo nhóm, thêm/bỏ thành viên, đổi trưởng nhóm, cấp/thu Toàn quyền, đặt trần, gửi tin, giao việc, huỷ việc. **Không một handler nào trong 26 cái đó tự kiểm quyền**: cả module dựa hoàn toàn vào `_auth_guard` và `_csrf_guard`. Xem Phần 12.2, mục (a).

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

**Cả ba file này không đổi một ký tự ở 0.153.0** (`mcp_catalog.py`, `mcp_hub.py`, `plugins_host.py` đều UNCHANGED trong diff). Cổng tool vì vậy vẫn đúng như khảo sát gốc mô tả, và vẫn là chỗ rẻ nhất để gắn principal.

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

*(0.153.0)* Không có chỗ khai báo `muc_quyen` thứ năm nào được thêm. Nhóm chat team **cố ý không dùng** `muc_quyen`: nó có trục riêng là trần của nhóm (`team_store.DEFAULT_CAPS`, `team_store.py:29-38`) cộng Toàn quyền của trưởng nhóm (`AUTH_CAPS`, `team_runner.py:69`). Đó là **trục thứ ba** trên cùng một hệ, xem Phần 12.2 mục (a).

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
ACTIONS = ("read_deliverable", "submit", "publish")                    # :23

def narrow(parent, request):        # :79  quyền con = PHẦN GIAO của cha và cái con xin
    ...
    acts = [a for a in _set(want_actions) if a in ACTIONS and a in _set(parent.get("actions"))]
    ...
    "write_paths": [x for x in _set(request.get("write_paths")) if x in _set(parent.get("write_paths"))],
    "read_paths":  [x for x in _set(request.get("read_paths"))  if x in _set(parent.get("read_paths"))],

def allows(grant, action, key):     # :103  "Thiếu gì cũng là không"
    if not grant or action not in ACTIONS or not key:
        return False
    if action not in _set(grant.get("actions")):
        return False
    paths = _set(grant.get("read_paths") if action == "read_deliverable" else grant.get("write_paths"))
    return key in paths

def path_key(brain_root, rel):      # :29  từ chối '..', đường tuyệt đối, ký tự điều khiển, symlink thoát
```

**Đây là mô hình capability có THU HẸP DẦN (attenuation)**, khoá theo đường dẫn, deny-by-default, và có một nguyên tắc bảo mật viết thẳng trong docstring: **không cấp quyền từ lời chat**.

Được lưu trong DB kèm `granted_by`, `generation`, `grant_events`, `scope_requests`; gọi từ **7 chỗ** trong `resonance_store.py:2430, 2453, 3434, 3780, 3821, 3901, 4307` (0.131.0: 7 chỗ ở số dòng cũ `2291, 2314, 3295, 3641, 3682, 3762, 4168`).

**File này KHÔNG đổi một ký tự ở 0.153.0**: vẫn đúng 137 dòng, vẫn `POLICY_VERSION = "grants.v1"`. **Kết luận:** không phải thiết kế mô hình mới. **Tổng quát hoá cái đang có và đang được test**, giữ nguyên `narrow()` và `allows()` vì chúng đã đúng.

---

## Phần 4. Mảnh còn thiếu, và chúng nhỏ hơn tưởng

### 4.1 "Ai" đã có một nửa

`server/resonance_store.py:374-383`:

```python
@dataclass(frozen=True)
class Principal:
    """Ai đang thao tác. `kind`: owner (người dùng qua auth của host) hoặc agent.
    Host tạo, model không tự khai."""
    kind: str
    id: str
    brain_id: str

    @property
    def by(self) -> str:
        return f"{self.kind}:{self.id}"
```

Nhưng nó vẫn được dựng bằng **literal cứng**: **15 chỗ** gọi `Principal(...)` ở 0.153.0 (0.131.0: 12), phân bố `main.py` 6, `resonance_api.py` 8, **`team_api.py` 1 (mới)**. Trong 15 chỗ đó, 11 chỗ là `Principal("owner", "owner", root)` literal; 4 chỗ đã truyền biến (`"agent", "javis"` ba lần và `"owner", by` một lần ở `main.py:22439`).

Class đã có, **không có đường nối từ session đăng nhập vào Principal**. Đó mới là việc phải làm. Chỗ nối gần nhất đã xuất hiện ở 0.153.0 là `main.py:22439`, nơi `by` đi vào làm `id` của principal chủ, nhưng nó vẫn là một chuỗi do tầng trên truyền xuống chứ không phải danh tính đọc từ session. Xem Phần 12.2 mục (b).

### 4.2 "Cái gì" đã có khoá chuẩn hoá

`server/capability_registry.py:34-40`:

```python
def brain_scope(brain):
    """Hash scope để Registry không lưu raw path và không trộn capability giữa hai brain."""
    raw = str(Path(brain or "brain").resolve()).casefold()
    return "brain_" + _sha(raw)[:20]
```

Đây là **khoá tài nguyên chuẩn hoá cho brain**, dùng xuyên suốt `capability_registry` và `memory_index`. Hàm `can(principal, action, resource)` không phải dựng từ số 0. **File này cũng không đổi ở 0.153.0.**

### 4.3 `turn_context` mang danh tính tới mọi tool hook, nhưng KHÔNG có luật nào đọc

`server/turn_context.py:10-11` (module **không đổi** ở 0.153.0):

```python
{"kenh": str, "sender_id": str, "chat_type": "private" | "group", "chat_id": str,
 "la_chu": bool}
```

`sender_id` = id nền tảng của người gửi tin **này**. `la_chu` = True trên bề mặt của chủ máy.

Docstring của module ghi thẳng động cơ tồn tại:

> *"a plugin wrapping an MCP that answers each person with their OWN permissions (a shop app where every staff member sees only their own work) had no way to tell which person a chatbot turn was answering"*

Tức **nhu cầu ABAC đã được cảm nhận trong mã**, và giải pháp lúc đó là thêm `sender_id`. Mối nối đã đặt sẵn; chỉ thiếu luật đọc nó.

*(0.153.0)* Điều này càng đúng hơn sau 0.140.0: nhóm chat team **cũng không bind `turn_context`**. `grep turn_context` trên `team_runner.py`, `team_engine.py`, `team_api.py`, `agent_jobs.py` không ra kết quả nào. Nghĩa là một lượt của trợ lý trong nhóm chạy mà **không có `sender_id` và không có `la_chu`**, y như trước. Xem Phần 12.2 mục (c).

Cảnh báo cho người đọc mã: `role` trong frontmatter agent **là câu mô tả persona**, không phải vai trò quyền. Đừng nhầm khi thiết kế. *(0.153.0)* Cảnh báo này nay có **ba** chỗ dễ nhầm chứ không một: `role` của agent, `role` của lượt nhóm (`TURN_ROLES`, `team_store.py:41`), và `role` của thành viên nhóm (`'leader'`/`'member'`, `team_store.py:57`). Chỉ cái thứ ba là gần với vai trò quyền, và nó vẫn không cấp quyền gì: Toàn quyền nằm ở bảng `team_authority` riêng.

### 4.4 `OWNER_ONLY_TOOLS`: tiền lệ duy nhất của "loại principal đổi danh sách tool"

`server/mcp_hub.py:102` (không đổi ở 0.153.0):

```python
# Tools that only the OWNER's own sessions may see (0.77.0). A dedicated bot at the "can write" /
# "full" level gets hub tools, and there the person steering the model is a stranger. ...
OWNER_ONLY_TOOLS = frozenset({"javis_reply_policy", "javis_reply_policy_tune"})
```

Đây là **chỗ DUY NHẤT** trong toàn repo mà loại principal đổi danh sách tool: một `frozenset` hai phần tử, lọc bằng cờ `for_bot`. Đây là tiền lệ để nhân bản thành bảng thật. *(0.153.0)* Vẫn đúng là chỗ duy nhất: `grep OWNER_ONLY_TOOLS` ra đúng `mcp_hub.py:1158-1159` (lọc) và `main.py:2837-2838` (lớp hai cho CLI), không có chỗ mới nào.

### 4.5 Phân quyền nền tảng kênh đã có, chỉ khác trục

`server/owner_channels.py:11`:

> *"Access is FAIL-CLOSED, like Zalo and unlike Telegram: an empty allow-list lets nobody in. A stranger who writes gets a 4-digit pairing code and lands in a queue the owner approves with one click."*

Lưu ý **Telegram thì fail-OPEN**: whitelist rỗng nghĩa là **không giới hạn ai** (`bot_gateway.py:87`). Đây là kiểm soát **gia nhập** (được nói chuyện không), không phải **uỷ quyền** (được làm gì). Một khi đã được nhận, mọi người có cùng toàn quyền.

*(0.153.0)* Danh sách kênh chủ không đổi (`ALL = (SLACK, WHATSAPP, DISCORD, LARK)`, `owner_channels.py:314`), nhưng **có thêm một kênh khách mới: Messenger** (`server/channels/messenger.py`, 0.134.0). Nó không đi qua `owner_channels` vì Messenger chỉ dành cho bot khách. Điểm đáng lưu ý cho multi-user: webhook của nó **công khai hoàn toàn** (`/messenger/webhook` nằm trong `_AUTH_PUBLIC_EXACT`, `main.py:228`) và thứ duy nhất đứng giữa Internet và một "tin từ chủ" là chữ ký `X-Hub-Signature-256` kiểm bằng app secret (`main.py:21340-21349`, `whatsapp_bot.verify_signature`). Cùng khuôn với WhatsApp, đã có test canh (`tests/python/test_messenger.py:327-329`: POST sai chữ ký và POST thiếu chữ ký đều 401 và không được parse).

---

## Phần 5. Chặn cứng: chọn brain KHÔNG bị giới hạn vào `BRAINS_DIR`

**Đây là vấn đề lớn nhất khi thêm người dùng thứ hai.**

`server/main.py:5858-5864` (**không đổi một ký tự** ở 0.153.0):

```python
def _brain_root(brain: str) -> str:
    if not brain or brain == "brain":
        return str(_default_brain_dir())
    b = brain[5:] if brain.startswith("path:") else brain
    return b if os.path.isdir(b) else str(_default_brain_dir())
```

Client gửi `brain = "path:/thư/mục/bất/kỳ"`, server nhận, và **chỉ kiểm "có phải thư mục tồn tại không"**.

`_safe_path` (`main.py:7288`) khoá đường dẫn **tương đối** trong brain root, nhưng **brain root do client chọn**. Docstring `_default_brain_dir` (`main.py:5846-5857`) xác nhận đây là tính năng có chủ đích: *"user vẫn chọn brain khác trong danh sách hoặc folder ngoài bất kỳ qua `path:<thư mục>`"*.

- Với **một** người dùng: tính năng hữu ích.
- Với **nhiều** người dùng: **thất bại cách ly hoàn toàn**. Người A gửi `path:` trỏ vào thư mục của người B, hoặc vào bất kỳ đâu trên đĩa.

**Sửa cái này là QUYẾT ĐỊNH THIẾT KẾ, không phải sửa bug.** Hai lựa chọn: bỏ `path:` khi có nhiều người dùng, hoặc biến nó thành tài nguyên có chủ (dùng `brain_scope` làm khoá). Phải chốt **trước** khi viết dòng RBAC nào.

### 5.1 Trần duyệt file đã được siết lại, nhưng `path:` thì không *(0.153.0)*

Đây là chỗ **phải đọc kỹ diff**, vì một nửa của vấn đề vừa được sửa và nửa còn lại thì không. Commit `ce101967` (0.153.0) đã bỏ nhánh *"localhost = chủ máy, cho tới ổ đĩa"* trong `_files_ceiling`:

```python
# BỎ:  elif not cfgmod.require_login():
#          ceil = Path(broot.anchor or broot)      # localhost = chủ máy → tới ổ đĩa
      if ceil is None:
          ceil = broot                            # Mặc định an toàn: luôn khoá trong brain
```

Đây là **cải thiện thật và đúng hướng cho multi-user**: trần duyệt mặc định từ nay là **chính brain**, không còn tuỳ vào việc server đang chạy localhost hay public. Trước đây một người dùng thứ hai ngồi cùng máy (hoặc bất kỳ ai vào được localhost) mặc nhiên có File Manager đi tới tận gốc ổ đĩa.

Đáng ghi lại vì nó là bài học: **test cũ đã khẳng định đúng cái hành vi sai**. `tests/python/test_files_root.py` bản 0.131.0 có nguyên một dòng

```python
check("localhost: đọc được file NGOÀI brain (trong ổ đĩa)", p == _TMP / "ngoai-vault.txt")
```

nên hành vi sai ấy được canh giữ như một tính năng. Bản mới đảo lại thành `check("localhost mặc định: trần = brain", ...)` và `check("localhost: chặn ../ ra ngoài brain", ...)`. Khi làm RBAC phải nhớ: **một test xanh không có nghĩa là một luật đúng**, nó chỉ nghĩa là mã đang làm đúng như người viết test tin. (Còn sót một chỗ nhỏ: docstring đầu `test_files_root.py:5` vẫn còn câu *"localhost mở tới ổ đĩa"*, đã lệch với thân test.)

**Nhưng phải nói rõ ba điều, kẻo đọc thành "Phần 5 đã xong":**

1. **`_brain_root` hoàn toàn không đụng tới.** Dòng `b = brain[5:] if brain.startswith("path:")` vẫn y nguyên (`main.py:5858-5864`). Siết trần duyệt **không** ngăn được client gửi `brain=path:/thư/mục/của/người/B`: brain root vẫn do client chọn, nên trần "khoá trong brain" chỉ khoá trong **brain mà kẻ gọi tự khai**. Đây vẫn là **thất bại cách ly nghiêm trọng nhất** và vẫn là **việc số 0** của lộ trình.
2. **Đường dẫn tuyệt đối của brain vẫn đi thẳng vào khoá phiên.** `_brain_key` (`main.py:5924-5938`) vẫn `Path(root).resolve()` rồi ghi nguyên chuỗi đó vào `sessions.brain`. Khoá này là **đường dẫn đĩa**, không phải id có chủ, nên nó không mang thông tin "brain này của ai".
3. Người dùng vẫn mở được ổ đĩa nếu tự đặt `JAVIS_FILES_ROOT=drive` (hoặc một đường dẫn tuyệt đối). Đó là **lựa chọn tường minh của chủ máy**, khác hẳn mặc định ngầm theo kiểu bind. Với multi-user thì biến môi trường này là **cấu hình toàn cục**, không phải ý muốn của từng người dùng, nên nó phải nằm trong danh sách phải chốt cùng `path:`.

---

## Phần 6. Tầng lưu trữ

### 6.1 Javis ĐÃ dùng SQLite, rất nhiều

Tiền đề "chưa có DB" là **sai**. Không có ORM, không có DBMS riêng, nhưng có tầng SQLite đáng kể:

- `import sqlite3` trong **12 module** `server/*.py` (0.153.0: **14**, thêm `team_migration.py` và `team_store.py`).
- **11+ database** riêng biệt, DDL viết tay, `PRAGMA journal_mode=WAL` gần như mọi store.
- Không có SQLAlchemy, psycopg, asyncpg, alembic, Redis, Mongo. `grep` trên `requirements.txt` cũng trắng. *(0.153.0)* Vẫn trắng: diff `requirements.txt` chỉ nâng đúng một dòng, `edge-tts>=7.0.0` → `edge-tts>=7.3.1` (Microsoft đổi cơ chế token handshake WebSocket của Edge Read Aloud, 7.2.8 về trước bị 403). **Không có dependency mới nào**, nên kết luận "không có ORM" càng chắc.

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
| **`team_store.py` (mới, 0.153.0)** | **`team.sqlite3`** | **10 bảng: `teams`, `team_members`, `team_events`, `team_steps`, `team_turns`, `agent_busy`, `team_reactions`, `team_authority`, `team_replies`, `team_reaction_log`** |

*(0.153.0)* `team_store.py` là **934 dòng** và là kho SQLite lớn nhất từng được thêm trong một lần. Nó có đủ nếp tốt của repo: `PRAGMA journal_mode=WAL` (`team_store.py:163`), `BEGIN IMMEDIATE` cho mọi giao dịch qua một helper `_Tx` (`team_store.py:166-173`), CAS thật cho hàng đợi và cho lượt. **Nhưng nó thêm một trục quyền mới hoàn toàn chưa có trong khảo sát gốc** (trần nhóm + Toàn quyền của trưởng nhóm, xem Phần 12.2 mục (a)), và bảng `team_authority` chính là một bảng cấp quyền thứ hai, **song song** với `grants` của A4. Khi làm RBAC phải quyết định gộp hay giữ tách.

### 6.2 Tầng JSON và JSONL

- **JSON blob, ghi nguyên tử** (`.tmp` + `os.replace`): `chatbot_store.py:257`, `plugins_host.py:178`, `config.py:611` (0.131.0: `606`), `inbox.py:86`, `mcp_store.py`.
- **JSON blob, ghi KHÔNG nguyên tử**: `config.py:1260` (`.sessions.json`, 0.131.0: `1252`) và `config.py:1327` (`.api_tokens.json`). **Đây là chỗ yếu nhất.**
- **JSONL append-only**: `usage-events.jsonl`, `mcp_audit.jsonl` (xoay ở 1 MB), `auth_audit.jsonl`, `chatbot-logs/<bot_id>.jsonl`.
- **Markdown trong git**: chính là brain, qua `git_brain.py`.

*(0.153.0)* Có thêm một file JSON blob mới: **`STATE_DIR/mcp-stdio-pids.json`** (`mcp_orphans.py:39`), sổ PID tiến trình MCP stdio để dọn tiến trình mồ côi (báo lỗi thực địa 2026-10-13: bot Zalo im lặng vì hai tiến trình `javis-zalo mcp start` cùng tranh một socket). Nó ghi **nguyên tử đúng khuôn** (`.tmp` + `os.replace`, `mcp_orphans.py:63-73`) và có `threading.Lock` (`mcp_orphans.py:40`). Nhưng nó **bị commit nhầm vào repo** kèm PID thật của máy dev (xem Phần 12.4).

### 6.3 Đồng thời: một tiến trình, nhiều thread

Server chạy **một** tiến trình uvicorn (`main.py:22138`, `reload=False`, **không** `--workers`). Rủi ro chính là thread-vs-thread. *(0.153.0)* Dòng này nay ở `main.py:22698`, **vẫn `reload=False` và vẫn không có `--workers`**: giả định một tiến trình vẫn đúng.

Cơ chế đang dùng:
- `threading.Lock` / `RLock` quanh mọi chu trình load-modify-save (43 chỗ).
- SQLite `BEGIN IMMEDIATE` + `busy_timeout` cho giao dịch thật.
- **Jitter-retry** ở `sessions.py:498-521` (0.131.0: `478-504`; 12 lần thử, `timeout=1.0` cố ý ngắn rồi tự thử lại) - khuôn mẫu cẩn thận nhất trong repo.
- `os.replace` nguyên tử cho JSON (28 chỗ).
- `asyncio.Lock` cho phía event loop.
- **`BrainLock`** (`git_brain.py:1272`, 0.131.0 cũng `1272` - **không đổi**): khoá file cấp HĐH duy nhất trong repo, `fcntl.flock` / `msvcrt.locking`, khoá theo brain tại `<brain>/.javis-learn.lock`. Docstring nêu lý do: *"asyncio.Lock không bảo vệ được tiến trình khác"*.

**Đánh giá rủi ro:** SQLite thì **thấp** (WAL + busy_timeout + BEGIN IMMEDIATE + lock + retry là chồng đủ vững trong một tiến trình). JSON blob thì **trung bình** (rename nguyên tử chống file hỏng nhưng **không** chống mất cập nhật; đúng đắn phụ thuộc mọi caller nhớ giữ lock). `config.py` session thì **cao nhất** (xem 1.3).

*(0.153.0)* Đánh giá này **không đổi**, kể cả với lượng dữ liệu mới. `team_store` đi đúng khuôn SQLite tốt (WAL + `BEGIN IMMEDIATE` + CAS), và rủi ro của nó thấp. Chỗ đáng chú ý là một dạng chờ mới được đưa vào có ý thức: **khoá bận theo agent** (`agent_busy`, `team_store.py:78-79`) giữ một lượt model mỗi agent trên **cả máy**, có `lease_until` để tự nhả khi tiến trình chết (`team_store.py:813-827`). Đây là hàng rào kiểu **lease**, khác hẳn lock trong tiến trình, và nó đúng là thứ cần cho multi-user về sau vì nó không giả định "một người dùng, một máy".

### 6.4 SQL là dialect SQLite thuần, không phải SQL chuẩn

Rải khắp mã: `INSERT ... ON CONFLICT(...) DO UPDATE`, `CREATE VIRTUAL TABLE ... USING fts5`, `PRAGMA table_info`, `AUTOINCREMENT`, placeholder `?`. FTS5 dùng cho tìm kiếm (`sessions.py`, `capability_registry.py`).

**Hệ quả:** chuyển PostgreSQL **không** phải đổi connection string. Đây là một cuộc port thật, kèm viết lại toàn bộ tầng tìm kiếm FTS5.

### 6.5 Dữ liệu rất nhỏ

DB lớn nhất trên máy khảo sát là `usage_index.db` khoảng **484 KB**. `conversations.db` 168 KB (WAL 1.2 MB). Đây là các file nhỏ.

**PostgreSQL vì dung lượng là không có cơ sở.**

### 6.6 Nếp migrate đã có sẵn và nhất quán

Không có framework migration, nhưng có **bảy khuôn mẫu thủ công** đang chạy tốt:

1. `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` lúc mở (`sessions.py:412-455` thêm 9 cột theo thời gian; 0.153.0: `sessions.py:428-464`).
2. Tuple `_MIGRATIONS` idempotent, bỏ qua lỗi "duplicate column" (`chatbot_reply_policy_store.py:84`).
3. Hằng `schema_version` trong bảng meta (`REGISTRY_SCHEMA_VERSION`, `INDEX_SCHEMA_VERSION`).
4. Hàm backfill có tên, chạy một lần (`resonance_store.py`, `_a2_migrate`, `_a4_migrate`).
5. Field `version` trong JSON + migrate-on-read kèm backup (`mcp_store.py:69-115`).
6. Cờ migrate trong config (`learn.py`, `_migrations` dict).
7. Cách ly file hỏng bằng đổi tên `*.corrupt-<timestamp>` (`capability_registry.py:186-192`; 0.153.0: `:189`).

Thêm bảng `users` và `grants` **đi theo đúng nếp này**, không cần dựng framework.

*(0.153.0) Khuôn mẫu thứ TÁM đã xuất hiện, và nó là khuôn mẫu đáng bắt chước nhất cho multi-user:* **chụp bản sao kho trước khi đổi hình dạng, rồi đánh dấu đã migrate bằng một dòng trong bảng riêng**. Có hai bản độc lập của cùng ý tưởng:

- `server/team_migration.py` (**39 dòng**, module mới): `snapshot_once(path, table, marker_column)` chép kho thành `<tên>.pre-0.140.0` **chỉ khi** bảng đã có mà cột đánh dấu chưa có, dùng SQLite backup API (lấy cả phần còn trong WAL, `team_migration.py:19-39`). Gọi từ `sessions.py:383` và `task_store.py:103`. Bản sao đã có thì **không ghi đè**; kho mới tinh thì **không chép**. Có cả công cụ khôi phục: `tools/team_chat_restore_pre.py` (200 dòng).
- `resonance_store.py`: `_backup_before()` (`:483`) cho A1/A2/A3/A4/A5, cộng bản mới nhất `.pre-0.142.0` khi gỡ người review cố định, và một **bảng `review_migrations`** ghi `done_at` để lần chạy sau không làm lại (`_remove_fixed_reviewers`, `resonance_store.py:428-475`).

Đây là **khuôn mẫu phải dùng khi thêm `users` và `grants`**: multi-user là loại thay đổi mà hạ phiên bản phải còn đường lùi, vì dữ liệu đã gắn với một người dùng cụ thể. Bản 0.153.0 làm đúng, và `_remove_fixed_reviewers` còn cẩn thận ở một chỗ đáng học: nó **không dùng lại** hàm `team_detach` sẵn có của chủ, vì hàm đó hẹn thức ngay và lần thức đó có thể kết thúc `succeeded` mà không ai review. Migration tự đánh dấu đạt là lỗi thật, và họ đã tránh nó.

### 6.7 Bí mật đang mã hoá, nhưng là store phẳng

`secrets_store.py`: Fernet (AES128-CBC + HMAC), tiền tố `enc:`, khoá ở `STATE_DIR/.secret_key`. Mất khoá thì giải mã ra `""` và người dùng nhập lại, cố ý fail-safe.

**Với multi-user:** bí mật của mỗi người phải được **namespace**, nếu không thì người này đọc được token của người kia.

*(0.153.0)* Store này **không đổi**, nhưng số người ghi vào nó thì tăng: `_SECRET_PATHS` (`config.py:513-530`) vẫn là danh sách **phẳng**, khoá theo đường dẫn trong `settings.json`, và nay có thêm `messenger.verify_token` (`config.py:225`). Đáng lưu ý là **verify token của Messenger KHÔNG nằm trong `_SECRET_PATHS`**, và đó là quyết định đúng: nó là chuỗi bắt tay Meta echo lại, không phải bí mật, nên nó hiện thẳng trên form (`main.py:21313-21326`). Nhưng **app secret của từng Page thì có** nằm trong `channel_accounts`, mã hoá qua `secrets_store` (`channel_accounts.py:158-167`). Nghĩa là bí mật của kênh khách đã đi qua **hai store khác nhau**: `settings.json` cho kênh chủ, `channel_accounts` cho tài khoản bot. Với multi-user, cả hai đều phải namespace.

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

1. **Principal** - mở rộng cái đã có ở `resonance_store.py:374`. Thêm `user_id` thật thay vì literal `"owner"`.
2. **Role (RBAC)** - `owner` / `editor` / `viewer` / `chatter`. Dùng cho giao diện và giá trị mặc định.
3. **Relation (ReBAC)** - bảng `grants(subject, relation, object)`: `user:7 → editor → brain:trolysoNguyenBan`. Đây là thứ cho phép chia sẻ brain.
4. **Policy (PBAC)** - hàm `can(principal, action, resource, context)` là **nơi DUY NHẤT** quyết định, tham chiếu cả role, relation, attribute (tool, `muc_quyen`, kênh, giờ). Viết bằng Python trong repo.

**Về thư viện:**
- **Casbin**: làm được RBAC + ABAC; ReBAC phải mô phỏng bằng role graph, hơi gượng.
- **OpenFGA** (Zanzibar): đúng ReBAC nhưng là service Go riêng, **phá triết lý self-host một tiến trình**.
- **Tự viết PEP**, tổng quát hoá `resonance_grants.py`: sạch hơn cả hai, vì luật của Javis rất đặc thù.

**Điểm mấu chốt:** chỉ có **một** hàm `can()`, gọi ở **hai** chỗ - `_auth_guard` (`main.py:288`) và `mcp_hub._guard` (`mcp_hub.py:212`). Không rải khắp 428 route.

*(0.153.0)* Đề xuất bốn tầng **giữ nguyên**, nhưng số điểm gọi phải đổi thành **ba**, và đây là phát hiện quan trọng nhất của lần khảo sát lại: từ 0.140.0 có **một hệ quyền thứ hai chạy song song** trong nhóm chat team (trần nhóm + Toàn quyền của trưởng nhóm), với bảng cấp quyền riêng `team_authority`. Nó **không** đi qua `_auth_guard` (vì đó là quyền giữa các agent trong một nhóm, không phải quyền HTTP) và **không** đi qua `mcp_hub._guard` (vì lượt nhóm chạy chỉ chữ, không có công cụ). Nó có điểm quyết định riêng ở `team_runner._apply_admin` (`team_runner.py:787-896`). Ba điểm quyết định là con số đúng, và `can()` phải là chỗ hợp nhất cả ba. Chi tiết ở Phần 12.2 mục (a).

---

## Phần 9. Lộ trình

Đã sắp lại sau ba vòng rà soát. Hai việc đầu nên làm **kể cả khi chưa multi-user**.

| # | Việc | Vì sao ở vị trí này |
|---|---|---|
| 0 | **Chốt chính sách `path:` trong `_brain_root`** (`main.py:5858`) | Là **quyết định thiết kế**, không phải code. Là thất bại cách ly nghiêm trọng nhất. Phải chốt trước khi viết RBAC. **0.153.0 đã siết trần duyệt file nhưng KHÔNG đụng `_brain_root`**, xem Phần 5.1: việc này vẫn nguyên. |
| 1 | **Sửa `_save_sessions`** (`config.py:1260`) và chuyển session sang SQLite | Lỗi đang tồn tại ở bản một người dùng: JSON hỏng thì đăng xuất hết mọi người. **0.153.0: vẫn chưa sửa**, hàm còn nguyên từng ký tự. |
| 2 | **Thêm `user_id` vào bản ghi session** | Đổi `{token: ts}` thành `{token: {ts, user_id}}`. Thay đổi nhỏ nhất. |
| 3 | **Nối `session → Principal`** | Thay literal `Principal("owner","owner",brain)`. 0.131.0: 7 chỗ. **0.153.0: 11 chỗ** (tổng số gọi `Principal` là 15). |
| 4 | **Bảng `users`** + đăng nhập nhiều tài khoản + 2FA theo user | Tách khối `auth.totp` ra từng người, mã hoá riêng từng secret. |
| 5 | **Bảng `grants(subject, relation, object)`** | Tổng quát hoá `resonance_grants`, **giữ nguyên** `narrow()` và `allows()`. **0.153.0: phải gộp thêm `team_authority`**, đừng để hai bảng cấp quyền song song (Phần 12.2 mục (a)). |
| 6 | **Thêm principal vào `effective_perm`** (`mcp_catalog.py:506`) | Mở rộng dàn `min()`, không đè tầng mới. File chưa đổi ở 0.153.0, nên đây vẫn là việc rẻ nhất. |
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

### 10.1 Bề mặt test đã đổi thế nào *(0.153.0)*

Tin tốt trước: **toàn bộ 20 file ở hai bảng trên KHÔNG đổi một dòng nào** giữa 0.131.0 và 0.153.0. Đã kiểm bằng `git diff --numstat` cho từng file. Nghĩa là bề mặt test một-admin **không bị xáo trộn**, và kế hoạch "viết lại khoảng 15 file" vẫn đúng nguyên quy mô.

Con số tổng: `tests/` từ **673 file `test_*`** (489 `.py` + 181 `.js`, 0.131.0) lên **712 file** (517 `.py` + 192 `.js`, 0.153.0), tức thêm **39 file test**. Trong 39 file mới đó, **20 file thuộc nhóm chat team** và là bề mặt multi-user thật sự mới:

- `test_team_identity.py` (63 dòng): danh tính bền `agent_key` cho mọi thành viên **mà không cần bật Cộng hưởng** (`agent_register`, `resonance_store.py:625`). Đây là mầm của khái niệm "người dùng" trong tương lai, chỉ khác là chủ thể đang là **agent** chứ chưa phải người.
- `test_team_authority.py` (372 dòng): trưởng nhóm Toàn quyền, trần, thu hồi.
- `test_team_api.py` (389 dòng): **có kiểm cách ly theo brain** (`test_team_api.py:99-100`, `:226`, `:302`, `:371`: brain khác trả 404 chứ không phải 403). Mô hình này đúng, và đáng bắt chước: **không tiết lộ sự tồn tại**.
- `test_team_rollback.py` (322 dòng) + `test_team_release.py` (123) + `tools/team_chat_restore_pre.py` (200): cả một bộ khôi phục phiên bản, đúng tinh thần Phần 6.6.
- `test_prerelease_0142.py`, `test_review_removed_0142.py`: canh hai migration một lần.

Điểm cần lưu ý khi lập kế hoạch: **cách ly brain trong nhóm đã được test, nhưng cách ly giữa người dùng thì chưa có gì để test**, vì chưa có người dùng thứ hai. Bộ test nhóm là **khuôn mẫu sẵn dùng** cho bộ test multi-user về sau.

---

## Phần 11. Phụ lục: điểm neo `file:line`

> **Cách đọc phụ lục này *(0.153.0)*.** Cột "Chỗ" ghi số dòng của **0.153.0** (commit `ce101967`), vì
> đó là bản đang chạy. Số của 0.131.0 nằm trong ngoặc khi khác. Kiểm bằng máy: **mọi dòng dưới đây
> đã được xác nhận tồn tại ở đúng số đó**, không phải suy ra từ diff.

### Danh tính và đăng nhập
| Việc | Chỗ |
|---|---|
| Khối `auth` duy nhất | `server/config.py:35` (**không đổi**) |
| `hash_password` PBKDF2 120k | `server/config.py:1034-1039` (0.131.0: `1030-1033`) |
| `verify_password`, `username_matches` | `server/config.py:1077-1100` (0.131.0: `1073-1093`) |
| `require_login` fail-closed theo bind | `server/config.py:1045-1060` (0.131.0: `1041-1054`) |
| `gate_active` | `server/config.py:1061-1065` (0.131.0: `1057-1059`) |
| `auth_enabled` | `server/config.py:1040-1044` (0.131.0: `1036-1038`) |
| `new_session` (không mang danh tính) | `server/config.py:1272-1278` (0.131.0: `1268-1272`) |
| `valid_session` | `server/config.py:1279-1288` (0.131.0: `1275-1282`) |
| `_save_sessions` **ghi không khoá, không nguyên tử** | `server/config.py:1260-1271` (0.131.0: `1252-1257`) |
| `_load_sessions` (JSON hỏng → `{}`) | `server/config.py:1244-1259` (0.131.0: `1240-1248`) |
| `SESSIONS = _load_sessions()` lúc import | `server/config.py:1257` (0.131.0: `1250`) |
| `provision_admin_from_env` | `server/config.py:1449-1517` (0.131.0: `1445-1510`) |
| `auth.totp.secret` trong `_SECRET_PATHS` | `server/config.py:528` (0.131.0: `522`) |
| `POST /auth/setup`, `/auth/login`, `/auth/password` | `server/main.py:1380`, `1418`, `1604` (0.131.0: `1375`, `1413`, `1599`) |
| Mã thiết lập đã bỏ (0.64.47) | `server/main.py:1382-1388` (0.131.0: `1381-1383`) |
| `clear_setup_token()` lúc boot | `server/main.py:12051` |
| Token API, scope `full`/`chat` | `server/config.py:1309-1396` (0.131.0: `1305-1399`); allowlist `TOKEN_SCOPE_CHAT` ở `:1313` |

### Cổng quyền
| Việc | Chỗ |
|---|---|
| `_AUTH_PUBLIC_PREFIX` / `_EXACT` / `_LOCAL_EXACT` | `server/main.py:215`, `217-228`, `238` (0.131.0: `213`, `215-223`, `233`) |
| `duong_dan_router` (vá PYSEC-2026-161) | `server/main.py:241-259` (0.131.0: `236-252`) |
| `_csrf_guard` | `server/main.py:261-286` (0.131.0: `255-279`) |
| `_auth_guard` (deny-by-default) | `server/main.py:288-312` (0.131.0: `282-305`) |
| `_token_ok` (nhánh Bearer) | `server/main.py:318-342` (0.131.0: `313-...)` |
| `WebSocketGuard` | `server/main.py:383`, `server/web_security.py:204-224` (**không đổi**) |
| `PERM_RANK` / `_MODE_CAP` | `server/mcp_catalog.py:29-30` (**không đổi**) |
| `effective_perm` (dàn `min()`) | `server/mcp_catalog.py:506-510` (**không đổi**) |
| `allowed` (lớp cứng) | `server/mcp_catalog.py:513-544` (**không đổi**) |
| **Chỗ gọi DUY NHẤT** | `server/mcp_hub.py:220` (**không đổi**) |
| `_guard` | `server/mcp_hub.py:212-248` (**không đổi**) |
| `OWNER_ONLY_TOOLS` | `server/mcp_hub.py:102` (**không đổi**); lọc ở `:1158-1159`, lớp hai cho CLI ở `main.py:2837-2838` |
| `VALID_MIN_MODE` / `_min_mode_ok` | `server/plugins_host.py:55`, `809-812` (**không đổi**) |
| `ActorPolicy` / `_hard_filter` | `server/capability_resolver.py:52-58`, `69-89` (**không đổi**) |
| `_permission_gate` (engine CLI) | `server/claude_sdk_engine.py:562-586` (**không đổi**) |

### Đã có sẵn để tái dùng
| Việc | Chỗ |
|---|---|
| **Luật quyền thuần (`narrow`, `allows`, `path_key`)** | `server/resonance_grants.py` (137 dòng, **không đổi**); `path_key:29`, `narrow:79`, `allows:103` |
| `Principal` (có cả `by`) | `server/resonance_store.py:374-384` (0.131.0: `371-379`) |
| `_owner_only` | `server/resonance_store.py:536-538` (0.131.0: `473-476`) |
| `brain_scope` (khoá tài nguyên chuẩn hoá) | `server/capability_registry.py:34` (**không đổi**) |
| `turn` dict (`sender_id`, `la_chu`) | `server/turn_context.py:10-11` (**không đổi**) |
| Ghi chú `owner_chat` / `chat_id` | `server/self_improve.py:318`, `server/task_store.py:143` (0.131.0: `self_improve.py:317`, `task_store.py:99`) |
| `"mine"` (nhóm giao diện, KHÔNG phải lọc truy cập) | `server/background_status.py:160` (**không đổi**) |
| **`agent_register` (danh tính bền, KHÔNG cần bật Auto)** *(mới)* | `server/resonance_store.py:625-639` |
| **`team_authority` (bảng cấp quyền thứ hai)** *(mới)* | `server/team_store.py:86-90`, `grant_authority:637`, `active_authority:670` |
| **`AUTH_CAPS` / `ADMIN_OPS` (trần của Toàn quyền)** *(mới)* | `server/team_runner.py:69`, `72-73` |
| **`_apply_admin` (điểm quyết định thứ ba)** *(mới)* | `server/team_runner.py:787-896`, `by = f"leader:{leader_key}"` ở `:796` |
| **`team_creator` (`you: True` cứng)** *(mới)* | `server/team_api.py:242-252`, `owner_name` ở `:51`, truyền từ `main.py:22452` |

### Rủi ro và chặn cứng
| Việc | Chỗ |
|---|---|
| **`_brain_root` không giới hạn vào `BRAINS_DIR`** | `server/main.py:5858-5864` (0.131.0: `5811-5815`, **không đổi**) |
| `_default_brain_dir` (xác nhận `path:` là chủ đích) | `server/main.py:5846-5857` (0.131.0: `5799-5809`) |
| `_brain_key` (khoá phiên = **đường dẫn đĩa**) | `server/main.py:5924-5938` |
| `_files_ceiling` (**đã siết, xem Phần 5.1**) | `server/main.py:7254-7281` |
| `_safe_path` / `_safe_serve_path` (chỉ khoá đường TƯƠNG ĐỐI) | `server/main.py:7287`, `7297` (0.131.0: `7220`, `7230`) |
| `/s/<token>` (chỉ token, không auth) | `server/share_store.py:83-92` (0.131.0: `63-91`) |
| `read_docs` từ chối thiếu header vault | `server/mcp_hub.py:1478-1482` (**không đổi**) |
| Kênh chủ: fail-closed (Slack/Zalo) vs fail-OPEN (Telegram) | `server/owner_channels.py:11`, `bot_gateway.py:87` |
| **Webhook Messenger công khai, chỉ chữ ký gác** *(mới)* | `server/main.py:21340-21349`; verify token không phải secret: `main.py:21313-21326` |
| **`server/mcp-stdio-pids.json` bị commit nhầm** *(mới)* | xem Phần 12.4 |

---

---

## Phần 12. Khảo sát lại ở 0.153.0: bốn phát hiện mới

Phần này **chỉ ghi cái ĐÃ ĐỔI** so với khảo sát gốc. Đọc xong Phần 12 là biết đủ để cập nhật kế hoạch, không cần đọc lại mã.

### 12.1 Nền của lần khảo sát lại

Diff từ `cf315765` (0.131.0) tới `ce101967` (0.153.0): **48 commit, 173 file, +29.203 dòng, -921 dòng**. `VERSION` từ `0.131.0` lên `0.153.0`. `requirements.txt` **chỉ đổi một dòng** (`edge-tts>=7.0.0` sang `>=7.3.1`), **không thêm dependency nào**.

**Lõi quyền không đổi một ký tự.** Đã kiểm bằng `git diff --numstat` cho từng file, tất cả **UNCHANGED**:

```
server/mcp_catalog.py         server/mcp_hub.py            server/resonance_grants.py
server/capability_registry.py server/capability_resolver.py server/turn_context.py
server/owner_channels.py      server/bot_gateway.py        server/plugins_host.py
server/secrets_store.py       server/totp.py               server/share_store.py
server/web_security.py        server/claude_sdk_engine.py  server/agent_assets.py
```

Và trong `config.py` / `main.py`, các hàm lõi cũng **giống hệt từng byte** (so bằng `md5sum` trên thân hàm): `hash_password`, `verify_password`, `username_matches`, `require_login`, `gate_active`, `auth_enabled`, `new_session`, `valid_session`, `_save_sessions`, `_load_sessions`, `_auth_guard`, `duong_dan_router`, `_csrf_guard`, `_token_ok`, `_brain_root`, `_default_brain_dir`, `_safe_path` (riêng `_files_ceiling` đổi đúng nhánh ở Phần 5.1).

**Hệ quả thực dụng:** mọi kết luận ở Phần 1 tới Phần 9 **không cần viết lại**. Việc phải làm là cập nhật **số dòng**, **điểm neo**, và **thêm một hệ quyền mới** vào bức tranh.

Quy mô mã đã lớn lên đáng kể, và đây là con số dùng để trả lời câu hỏi "viết lại bằng Django tốn bao nhiêu":

| Phần | 0.131.0 | 0.153.0 | Chênh |
|---|---|---|---|
| `server/*.py` | 117.461 dòng / 186 file | **124.620 dòng / 196 file** | +7.159 |
| `dashboard/*.js` | 40.697 / 71 | **45.021 / 77** | +4.324 |
| `tests/*.py` | 109.317 / 499 | **116.417 / 527** | +7.100 |
| `tests/*.js` | 23.569 / 182 | **25.600 / 193** | +2.031 |
| file `test_*` | 673 | **712** | +39 |
| handler trong `server/` | 394 | **428** | +34 |

Ghi chú về cách đếm, vì con số cũ trong Phần 7.1 (`133.927` dòng Python `server/` + `40.540` JS + `673` file test) **không khớp với bất kỳ cách đếm nào tái lập được**: `server/*.py` chỉ có 117.461 dòng ở 0.131.0, `dashboard/*.js` là 40.697, và `673` thì đúng là số file `test_*` nhưng `tests/` có 692 file. Ba con số đó có vẻ được lấy từ ba phạm vi khác nhau trong ba lần chạy khác nhau. Bảng trên đếm nhất quán một cách (`git show` từng file rồi cộng dòng), nên dùng bảng này. **Kết luận không đổi**: đây là **viết lại**, không phải migration, và tỉ lệ đó chỉ xấu thêm sau 0.153.0.

### 12.2 Phát hiện 1: một hệ quyền THỨ HAI đã mọc lên

**Đây là phát hiện quan trọng nhất, và nó không có trong khảo sát gốc.**

Khảo sát gốc kết luận có **hai** cổng quyền: HTTP (`_auth_guard`) và tool (`mcp_hub._guard`). Từ 0.140.0 có **cổng thứ ba**, và nó không đi qua hai cổng kia.

**Trần của nhóm** (`team_store.DEFAULT_CAPS`, `team_store.py:29-38`): `task_turns`, `task_steps`, `help_depth`, `no_progress`, `mentions`, `daily_turns`, `running_jobs`, `busy_wait_s`. Đây là mức mặc định của mọi nhóm.

**Toàn quyền của trưởng nhóm** (`team_store.team_authority`, `team_store.py:86-90`): một bảng **cấp quyền** riêng, có `grant_json`, `granted_by`, `granted_at`, `revoked_at`, `revoke_reason`. Mỗi nhóm tối đa một dòng hiệu lực (`grant_authority`, `team_store.py:637-655`). Trưởng nhóm Toàn quyền đổi được trần **trong mức trần trần chủ đặt** (`update_authority_caps`, `team_store.py:681-696`), nhưng **không tự nâng mình lên**: route cấp Toàn quyền là route dashboard, `by="owner"` cứng, và không lệnh `JAVIS_TEAM` nào gọi tới (`team_api.py:770-793`).

**Điểm quyết định thứ ba** là `team_runner._apply_admin` (`team_runner.py:787-894`). Cổng của nó ở `:791-794`:

```python
auth = self.teams.active_authority(team_id)
if not auth or auth["leader_key"] != leader_key:
    notes.append(f"Lệnh '{op.get('op')}' cần Toàn quyền, đã bỏ")
    return
```

**Vì sao nó không nằm trong hai cổng cũ:**
- Không qua `_auth_guard`: đây là quyền **giữa các agent trong một nhóm**, không phải quyền HTTP. Người gọi là model, không phải trình duyệt.
- Không qua `mcp_hub._guard`: lượt nhóm chạy **chỉ chữ, không có công cụ nào** (`team_engine.py`: giữ nguyên giới hạn của lượt nhóm theo phán quyết R3, chỉ chữ, thư mục trống, không MCP, không hub). Không có tool nào để `allowed()` chặn.

**Chín lệnh quản trị** (`ADMIN_OPS`, `team_runner.py:72-73`): `open_job`, `reprioritize`, `add_member`, `remove_member`, `create_agent`, `set_cap`, `set_resonance`, `edit_agent`, `interrupt`. Trần **10 lệnh mỗi lượt** (`ADMIN_OPS_PER_TURN`, `:71`). Bốn trần đổi được (`AUTH_CAPS`, `:69`): `task_turns`, `task_steps`, `help_depth`, `daily_turns`.

**Hệ quả cho kế hoạch RBAC, phải sửa Phần 8:**

`can(principal, action, resource, context)` giờ có **ba** điểm gọi, không phải hai: `_auth_guard`, `mcp_hub._guard`, và `team_runner._apply_admin`. Và bảng `grants` ở việc số 5 của lộ trình phải **gộp cả `team_authority`**, nếu không sẽ có hai bảng cấp quyền song song cùng tồn tại, đúng thứ mà khảo sát gốc cảnh báo.

**Điểm sáng:** hệ mới này làm đúng vài điều mà khảo sát gốc mong muốn:
- **Trần không bỏ được bằng số lớn**: "Không giới hạn" lưu là `null` kèm cờ `unlimited=true` (`_within`, `team_runner.py:706-713`), đúng như thiết kế A5 đã chốt.
- **Thu hồi là một trạng thái, không phải xoá**: `revoked_at` + `revoke_reason`, giữ nguyên hồ sơ.
- **CAS thật**: `update_authority_caps` trả `False` nếu dòng đã bị thu hồi giữa đường.
- **Ai làm gì đều có `by`**: `by = f"leader:{leader_key}"` (`team_runner.py:796`) chảy vào mọi bảng có cột `by`.

### 12.3 Phát hiện 2: danh tính "ai đang thao tác" đã có chỗ nối, nhưng nó là CHUỖI, không phải khoá

Khảo sát gốc nói `Principal` "được dựng bằng literal cứng". Điều đó vẫn đúng, nhưng nay đã có **một chỗ nối thật**, và nó chỉ ra đúng chỗ cần sửa.

`main.py:22436-22439`, hàm `_TeamAdmin.set_resonance`:

```python
def set_resonance(self, root, slug, on, by):
    if on and _agent_md_path(root, slug) is None:
        raise FileNotFoundError(slug)
    _resonance_store().agent_set_enabled(resonance_store.Principal("owner", by, _brain_key(root)), slug, bool(on))
```

Đây là **chỗ duy nhất trong repo** mà trường `id` của `Principal` **không** phải literal `"owner"`. `by` chảy vào từ `_apply_admin` với giá trị `"leader:<agent_key>"`, rồi đi tiếp vào cột `by` của bảng `resonance_agent_events` (`resonance_store.py:543-546`) và vào `_bump_agent` (`:565-571`).

**Vì sao đây là phát hiện đáng chú ý, và cũng là chỗ dễ sai:**

Điều kiện để qua cổng là `p.kind != "owner"` (`_owner_only`, `resonance_store.py:536-538`). Mà `_apply_admin` dựng `Principal("owner", by, ...)`, tức `kind` **luôn** là `"owner"`. Nên cổng `_owner_only` **luôn cho qua** trên đường này, và thứ duy nhất phân biệt người gọi là **chuỗi `id`**. Một **agent** (trưởng nhóm Toàn quyền) đang thao tác dưới `kind="owner"`, và dấu vết duy nhất là `id = "leader:ag_..."`.

Đây chính xác là hình dạng mà khảo sát gốc gọi là "literal cứng", nhưng ở dạng nguy hiểm hơn: không phải thiếu danh tính, mà là **danh tính đúng bị nhét vào một trường không được kiểm**. Khi làm RBAC phải sửa cả hai đầu:
1. `kind` phải phân biệt được `owner` thật với `leader:<key>`, nếu không thì `_owner_only` là cổng rỗng trên đường này.
2. `id` phải là **khoá có cấu trúc**, không phải chuỗi ghép, nếu không thì không truy vấn ngược được "agent này đã làm gì".

**Số chỗ gọi `Principal` đã tăng**: 12 (0.131.0) lên **15** (0.153.0), phân bố `main.py` 6, `resonance_api.py` 8, `team_api.py` 1. Trong đó **11 chỗ vẫn là literal `Principal("owner", "owner", root)`**. Việc số 3 của lộ trình từ "thay 7 chỗ" thành **"thay 11 chỗ, và sửa `kind` ở chỗ thứ 12"**.

### 12.4 Phát hiện 3: một file dữ liệu chạy đã lọt vào git, và `.gitignore` KHÔNG cứu được

`server/mcp-stdio-pids.json` là **sổ PID tiến trình MCP stdio**, sinh ra lúc chạy (`mcp_orphans.py:39`). Nó **đã bị commit** ở `063750ac` (0.141.0) với nội dung `{}`.

Điều đáng nói không phải là nó bị commit, chuyện đó nhỏ. Điều đáng nói là **cách nó được xử lý, và cách đó không hiệu quả**:

1. `.gitignore` **đã có** luật cho nó, thậm chí **hai lần** (`HEAD:.gitignore:79` và `:191`), nhưng luật `.gitignore` **không áp cho file đã tracked**. File đã vào index thì `.gitignore` vô hiệu với nó, đúng như thiết kế của git.
2. Có dấu hiệu ai đó đã nhận ra và xử lý bằng **`git update-index --assume-unchanged`**: `git ls-files -v` trả cờ chữ thường `h` cho đúng file này, và nó là **file duy nhất trong toàn repo** mang cờ đó. Kiểm chứng: index giữ `{}` (blob `9e26dfee`), còn worktree đang là 631 byte dữ liệu thật (`git hash-object` ra `2dbfe1aa`), và `git status` **không** báo gì.

**Vì sao đây là vấn đề với multi-user, không phải chuyện gọn gàng:**

- `--assume-unchanged` là **cờ cục bộ theo máy, không được commit**. Máy khác clone về **không** có cờ đó, nên lần chạy đầu tiên của họ làm file bẩn, và lần `git add -A` nào đó sẽ **commit PID của họ** lên. Đây là loại lỗi chỉ nổ ở máy người khác.
- Nó che mất **dữ liệu vận hành** khỏi mắt `git status`. Một file 631 byte chứa PID thì không sao. Nhưng đây là **tiền lệ**: cùng cơ chế đó áp lên một file chứa token hay đường dẫn brain thì không ai phát hiện.
- Với multi-user, câu hỏi "máy này đang chạy tiến trình nào cho **ai**" trở thành câu hỏi thật, và câu trả lời không nên nằm trong git.

**Việc phải làm (nhỏ, nên làm sớm, tách hẳn khỏi RBAC):** `git rm --cached server/mcp-stdio-pids.json`, bỏ cờ assume-unchanged, giữ nguyên luật `.gitignore`. Đây là **cùng loại việc với `_save_sessions`**: sửa kể cả khi chưa multi-user, vì nó là nợ đang chạy.

### 12.5 Phát hiện 4: `turn_context` vẫn chưa được dùng, và nay có thêm một bề mặt không dùng nó

Khảo sát gốc ghi: `turn_context` mang `sender_id` và `la_chu` tới mọi tool hook, nhưng không luật nào đọc. **Điều đó vẫn đúng, và bề mặt chưa dùng nó đã rộng hơn.**

Đã kiểm: `grep -c turn_context` trên `team_runner.py`, `team_engine.py`, `team_api.py`, `agent_jobs.py` đều trả **0**. Nghĩa là **mọi lượt nhóm chat team và mọi việc giao cho trợ lý chạy mà không có `sender_id`, không có `la_chu`**.

Đối chiếu với các chỗ **có** bind (`main.py:15756`, `:15822`, `:19271`; `mcp_hub.py:1447`; `plugins_host.py:1048`), ta thấy quy luật: `turn_context` được bind ở **đường chat**, không phải ở **đường việc nền**. Nhóm chat team là đường thứ ba, và nó rơi vào khe.

**Vì sao quan trọng cho multi-user:** khi thêm người dùng thứ hai, câu hỏi đầu tiên của mọi luật ABAC là *"lượt này đang trả lời AI"*. Với nhóm chat team, câu trả lời hiện là **không ai**, nên không luật nào viết được cho đường đó. Đây là **việc phải làm TRƯỚC** khi viết luật, không phải sau.

**Một chi tiết tích cực cần ghi lại:** ở một chỗ khác, hệ nhóm **đã** làm đúng điều này theo cách riêng. `team_store.team_members` có cột `identity_json` (`team_store.py:142-145`), chụp lại `{slug, name, avatar}` **theo `agent_key`** tại lúc còn xác nhận được (`set_identity`, `team_store.py:343-348`), để tin cũ giữ đúng người viết sau khi trợ lý bị xoá và slug được dùng lại cho trợ lý khác (review `4f6268bd`). Đây là **bản chụp danh tính**, đúng thứ mà `turn_context` đang thiếu: một danh tính **bền**, không phải một id tra cứu lại mỗi lần. Khi thiết kế `users`, nên theo khuôn này.

Và ở `team_store.py:151-152` có một dòng comment nói thẳng giới hạn hiện tại, đáng trích nguyên văn vì nó là **điểm khởi đầu đúng** cho việc thêm người dùng:

> *"Người tạo nhóm (0.142.0): người thật, lấy từ phiên xác thực của server, không từ client. Javis hiện có một tài khoản chủ máy nên nhóm cũ nhận đúng chủ đó ("owner"), **không giả lập đa người dùng**."*

Nghĩa là chỗ này **đã lấy danh tính từ phiên xác thực** (đúng hướng), và chỉ dừng lại vì chưa có người dùng thứ hai. Khi có bảng `users`, đây là một trong ít chỗ **không phải sửa logic, chỉ phải bỏ chú thích**.

### 12.6 Việc phải làm, cập nhật sau khảo sát lại

Giữ nguyên thứ tự ở Phần 9, thêm bốn việc mới. Việc **0**, **1** và **mới-1** nên làm **kể cả khi chưa multi-user**.

| # | Việc | Thay đổi so với Phần 9 |
|---|---|---|
| 0 | Chốt chính sách `path:` trong `_brain_root` (`main.py:5858`) | **Không đổi.** 0.153.0 siết `_files_ceiling` nhưng không đụng `_brain_root` (Phần 5.1). |
| 1 | Sửa `_save_sessions` (`config.py:1260`) | **Không đổi.** Hàm còn nguyên từng byte. |
| **mới-1** | **Gỡ `server/mcp-stdio-pids.json` khỏi git** (`git rm --cached`, bỏ cờ assume-unchanged) | **Mới** (Phần 12.4). Nhỏ, độc lập, cùng loại nợ với việc 1. |
| **mới-2** | **Bind `turn_context` cho lượt nhóm và việc giao trợ lý** | **Mới** (Phần 12.5). Phải xong TRƯỚC khi viết luật ABAC nào, vì luật cần `sender_id`. |
| **mới-3** | **Sửa `Principal("owner", by, ...)` ở `main.py:22439`** để `kind` phân biệt `owner` thật với `leader:<key>` | **Mới** (Phần 12.3). `_owner_only` hiện là cổng rỗng trên đường này. |
| 2 | Thêm `user_id` vào bản ghi session | Không đổi. |
| 3 | Nối `session → Principal` | **7 chỗ thành 11 chỗ literal** (tổng 15 chỗ gọi `Principal`). |
| 4 | Bảng `users` + 2FA theo user | Không đổi. |
| 5 | Bảng `grants(subject, relation, object)` | **Phải gộp cả `team_authority`** (Phần 12.2). |
| 6 | Thêm principal vào `effective_perm` (`mcp_catalog.py:506`) | **Không đổi, vẫn là việc rẻ nhất**: file chưa đổi một ký tự. |
| **mới-4** | **`can()` phải có BA điểm gọi**, thêm `team_runner._apply_admin` (`team_runner.py:787`) | **Mới** (Phần 12.2). Phần 8 ghi hai, phải sửa thành ba. |
| 7 | PostgreSQL (nếu thật sự cần) | Không đổi. |

**Kết luận của lần khảo sát lại, một câu:** ba kết luận lớn của khảo sát gốc vẫn đúng và còn nguyên giá trị (giữ FastAPI + SQLite, tổng quát hoá `resonance_grants`, chặn cứng là `_brain_root`), nhưng bức tranh quyền đã phức tạp hơn một bậc: **có ba điểm quyết định và hai bảng cấp quyền**, và việc phải làm trước tiên vẫn là ba việc nhỏ, độc lập, không liên quan gì tới framework.

---

## Tóm tắt một khối

1. **Một người dùng duy nhất, thật sự.** `cfg["auth"]` là một dict; scope duy nhất của một credential là token API hai giá trị trên allowlist bốn đường.
2. **Hai cổng quyền, và cả hai đã tốt hơn vẻ ngoài.** HTTP: deny-by-default, **15** đường miễn trên **428** handler. Tool: một hàm `allowed()`, một chỗ gọi, một dàn `min()`.
3. **Mô hình quyền đúng đã có sẵn** ở `resonance_grants.py`: capability thu hẹp dần, deny-by-default, khoá theo đường dẫn, không đọc lời chat. Việc phải làm là tổng quát hoá, không phải phát minh. **File này vẫn 137 dòng và không đổi một ký tự ở 0.153.0.**
4. **"Ai" và "cái gì" đều đã có một nửa**: `Principal` đã có class nhưng dựng bằng literal (**15 chỗ gọi, 11 còn literal**); `brain_scope` đã có khoá chuẩn hoá; `turn_context` đã mang `sender_id` tới mọi hook nhưng không luật nào đọc, **và lượt nhóm không bind nó**.
5. **Chặn cứng là `_brain_root`**, không phải thiếu RBAC. Phải chốt chính sách `path:` trước. **0.153.0 đã siết trần duyệt file nhưng không đụng `_brain_root`, nên việc này vẫn nguyên.**
6. **`_save_sessions` là lỗi đang tồn tại**, nên sửa kể cả khi chưa multi-user. **0.153.0 vẫn chưa sửa.**
7. **Giữ FastAPI + SQLite.** Django/DRF thay tầng không thiếu và không cho tầng đang thiếu. PostgreSQL tách riêng.
8. **Mới ở 0.153.0: hệ quyền thứ hai đã mọc lên** trong nhóm chat team (`team_authority` + trần nhóm + Toàn quyền trưởng nhóm), với điểm quyết định riêng ở `team_runner._apply_admin`. `can()` phải hợp nhất **ba** điểm, không phải hai. Chi tiết ở Phần 12.2.
