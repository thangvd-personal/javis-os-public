"""Kiem thu tu dong cho he thong multi-user auth va phan tach state:
1. Tach thong tin dang nhap vao auth.json va loai khoi settings.json
2. Bo sung user_id (dinh dang UUID) vao cau hinh auth
3. Phan tach va cach ly thu muc trang thai server/[user_id]/state/
4. Xac thuc credential doc lap giua cac user (multi-user credential isolation)
5. Kiem thu cac endpoint FastAPI (/auth/login, /auth/status) cho multi-user
"""
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

_fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


def run_tests():
    # Su dung thu muc state rieng biet de khong anh huong den runtime that
    test_state_root = tempfile.mkdtemp(prefix="javis-multi-user-test-")
    os.environ["JAVIS_STATE_DIR"] = test_state_root
    os.environ["JAVIS_REQUIRE_LOGIN"] = "1"
    os.environ.pop("JAVIS_ADMIN_PASSWORD", None)
    os.environ.pop("JAVIS_ADMIN_USER", None)

    try:
        import config as c

        # ------------------------------------------------------------
        # 1. Tach thong tin dang nhap vao auth.json
        # ------------------------------------------------------------
        pw1 = "ThangPass123!"
        h1, salt1 = c.hash_password(pw1)
        uid1 = str(uuid.uuid4())
        user1 = {
            "user_id": uid1,
            "username": "thangvd",
            "password_hash": h1,
            "salt": salt1,
            "totp": {"enabled": False, "secret": "", "recovery": [], "last_step": 0},
        }

        # Ghi user1 vao auth qua save_user
        c.save_user(user1)

        auth_path = c.get_auth_path()
        check("auth.json ton tai", auth_path.exists())

        raw_auth = json.loads(auth_path.read_text(encoding="utf-8"))
        check("auth.json co truong user_id", bool(raw_auth.get("user_id")))
        check("auth.json co username dung", raw_auth.get("username") == "thangvd")
        check("auth.json co password_hash", raw_auth.get("password_hash") == h1)

        # Kiem tra settings.json khong chua khoi 'auth' tren dia
        cfg = c.read_settings()
        cfg["workspace_name"] = "Multi-User Test Workspace"
        c.write_settings(cfg)

        raw_settings = json.loads(c.SETTINGS_PATH.read_text(encoding="utf-8"))
        check("settings.json tren dia KHONG chua khoi auth", "auth" not in raw_settings)

        # Nhung read_settings() van tra ve auth hop le de tuong thich nguoc
        loaded_cfg = c.read_settings()
        check("read_settings() van tra ve auth tu auth.json", loaded_cfg.get("auth", {}).get("username") == "thangvd")

        # ------------------------------------------------------------
        # 2. Dinh dang UUID cua user_id
        # ------------------------------------------------------------
        parsed_uuid = uuid.UUID(raw_auth.get("user_id"))
        check("user_id la UUID hop le (version 4)", parsed_uuid.version == 4)

        # ------------------------------------------------------------
        # 3. Phan tach thong tin dang nhap multi-user
        # ------------------------------------------------------------
        pw2 = "AliceSecret456!"
        h2, salt2 = c.hash_password(pw2)
        uid2 = str(uuid.uuid4())
        user2 = {
            "user_id": uid2,
            "username": "alice",
            "password_hash": h2,
            "salt": salt2,
            "totp": {"enabled": False, "secret": "", "recovery": [], "last_step": 0},
        }
        c.save_user(user2)

        pw3 = "BobSecret789!"
        h3, salt3 = c.hash_password(pw3)
        uid3 = str(uuid.uuid4())
        user3 = {
            "user_id": uid3,
            "username": "bob",
            "password_hash": h3,
            "salt": salt3,
            "totp": {"enabled": False, "secret": "", "recovery": [], "last_step": 0},
        }
        c.save_user(user3)

        all_users = c.list_users()
        check("danh sach users co du 3 user", len(all_users) >= 3)

        u_alice = c.get_user(username="alice")
        check("tim duoc user alice theo username", u_alice is not None and u_alice.get("user_id") == uid2)

        u_alice_case = c.get_user(username="ALICE")
        check("tim duoc user khong phan biet hoa thuong", u_alice_case is not None and u_alice_case.get("user_id") == uid2)

        u_by_id = c.get_user(user_id=uid3)
        check("tim duoc user theo user_id", u_by_id is not None and u_by_id.get("username") == "bob")

        # Kiem tra xac thuc mat khau tung user
        check("user1 dung mat khau -> hop le", c.verify_password_for_user("thangvd", pw1) is not None)
        check("user1 nhap mat khau user2 -> that bai", c.verify_password_for_user("thangvd", pw2) is None)
        check("user2 dung mat khau -> hop le", c.verify_password_for_user("alice", pw2) is not None)
        check("user2 nhap mat khau user1 -> that bai", c.verify_password_for_user("alice", pw1) is None)
        check("user3 dung mat khau -> hop le", c.verify_password_for_user("bob", pw3) is not None)
        check("sai mat khau -> that bai", c.verify_password_for_user("bob", "wrongpass") is None)
        check("user khong ton tai -> that bai", c.verify_password_for_user("charlie", "any") is None)

        # ------------------------------------------------------------
        # 4. Cach ly thu muc trang thai server/state/[user_id]/
        # ------------------------------------------------------------
        dir1 = c.get_user_state_dir(uid1)
        dir2 = c.get_user_state_dir(uid2)

        # uid1 is the PRIMARY account (first one saved): it keeps the legacy state folder so an
        # upgraded install finds its data and .secret_key where they were. Others get <root>/<uid>.
        check("primary account keeps the legacy state folder", dir1 == c.Path(test_state_root).resolve())
        check("duong dan state user2 chua user_id2", uid2 in str(dir2))
        check("hai thu muc state phai khac nhau hoan toan", dir1 != dir2)

        # Kiem tra quy uoc duong dan mac dinh server/state/[user_id]
        check("second account lives in <root>/<user_id>", dir2 == c.Path(test_state_root).resolve() / uid2)

        # Thu ghi du lieu vao state user1
        f1 = dir1 / "user_data.txt"
        f1.write_text("du lieu rieng cua user1", encoding="utf-8")

        f2 = dir2 / "user_data.txt"
        check("du lieu user1 khong bi lan sang user2", not f2.exists())

        # ------------------------------------------------------------
        # 5. Cap nhat va xoa user
        # ------------------------------------------------------------
        new_pw2 = "NewAlicePass999!"
        h2_new, salt2_new = c.hash_password(new_pw2)
        user2_updated = dict(user2, password_hash=h2_new, salt=salt2_new)
        c.save_user(user2_updated)

        check("user2 dang nhap duoc bang mat khau moi", c.verify_password_for_user("alice", new_pw2) is not None)
        check("user2 khong the dung mat khau cu", c.verify_password_for_user("alice", pw2) is None)
        check("user1 khong bi anh huong khi user2 doi mat khau", c.verify_password_for_user("thangvd", pw1) is not None)

        # Xoa user3
        check("xoa user3 thanh cong", c.delete_user(uid3))
        check("user3 khong con ton tai trong he thong", c.get_user(user_id=uid3) is None)
        check("user3 khong the dang nhap", c.verify_password_for_user("bob", pw3) is None)

        # ------------------------------------------------------------
        # 6. Kiem tra endpoint FastAPI va Session mang danh tinh
        # ------------------------------------------------------------
        import main
        from fastapi.testclient import TestClient

        client = TestClient(main.app, client=("127.0.0.1", 50000), base_url="http://localhost:7777")

        # Dang nhap user1
        r1 = client.post("/auth/login", data={"username": "thangvd", "password": pw1})
        check(f"API login user1 thanh cong (status {r1.status_code})", r1.status_code == 200 and r1.json().get("ok"))
        tok1 = r1.cookies.get("javis_session")
        check("login user1 co cookie javis_session", bool(tok1))
        check("session user1 mang dung user_id1", c.get_session_user_id(tok1) == uid1)
        check("session user1 mang dung username thangvd", (c.get_session_info(tok1) or {}).get("username") == "thangvd")

        # Dang nhap user2 bang mat khau moi
        r2 = client.post("/auth/login", data={"username": "alice", "password": new_pw2})
        check(f"API login user2 thanh cong (status {r2.status_code})", r2.status_code == 200 and r2.json().get("ok"))
        tok2 = r2.cookies.get("javis_session")
        check("login user2 co cookie javis_session", bool(tok2))
        check("session user2 mang dung user_id2", c.get_session_user_id(tok2) == uid2)
        check("session user2 mang dung username alice", (c.get_session_info(tok2) or {}).get("username") == "alice")
        check("hai token session cua 2 user doc lap", tok1 != tok2)

        # Kiem tra /auth/status phan biet dung user
        st1 = client.get("/auth/status", cookies={"javis_session": tok1}).json()
        check("status user1 tra ve dung user_id", st1.get("user_id") == uid1 and st1.get("username") == "thangvd")

        st2 = client.get("/auth/status", cookies={"javis_session": tok2}).json()
        check("status user2 tra ve dung user_id", st2.get("user_id") == uid2 and st2.get("username") == "alice")

        # Dang nhap user2 bang sai mat khau
        main._LOGIN_FAILS.clear()
        r_fail = client.post("/auth/login", data={"username": "alice", "password": "wrongpassword"})
        check("API login sai mat khau tra ve 401", r_fail.status_code == 401)

        # Kiem tra tao user qua API /auth/users
        r_new_user = client.post("/auth/users", data={"username": "charlie", "password": "PasswordCharlie123!"}, cookies={"javis_session": tok1})
        check("API tao user moi charlie thanh cong", r_new_user.status_code == 200 and r_new_user.json().get("ok"))
        charlie_uid = r_new_user.json().get("user_id")

        r_users = client.get("/auth/users", cookies={"javis_session": tok1}).json()
        u_names = [u.get("username") for u in r_users.get("users", [])]
        check("danh sach users qua API chua charlie", "charlie" in u_names)

        # User charlie vua tao dang nhap duoc ngay
        r_charlie_login = client.post("/auth/login", data={"username": "charlie", "password": "PasswordCharlie123!"})
        check("user charlie dang nhap thanh cong", r_charlie_login.status_code == 200 and r_charlie_login.json().get("ok"))
        tok_charlie = r_charlie_login.cookies.get("javis_session")
        check("session charlie mang dung user_id", c.get_session_user_id(tok_charlie) == charlie_uid)

        # Kiem tra doi mat khau cho user2 qua /auth/password
        pw_alice_3 = "AlicePassV3#123"
        r_change_pw = client.post("/auth/password", data={"current_password": new_pw2, "password": pw_alice_3}, cookies={"javis_session": tok2})
        check("alice doi mat khau thanh cong", r_change_pw.status_code == 200 and r_change_pw.json().get("ok"))

        # Alice dang nhap duoc bang mat khau v3
        r_alice_v3 = client.post("/auth/login", data={"username": "alice", "password": pw_alice_3})
        check("alice dang nhap duoc bang mat khau v3", r_alice_v3.status_code == 200 and r_alice_v3.json().get("ok"))
        tok_alice_v3 = r_alice_v3.cookies.get("javis_session")

        # User1 van dang nhap binh thuong voi pw1
        r_user1_still_ok = client.post("/auth/login", data={"username": "thangvd", "password": pw1})
        check("user1 van dang nhap binh thuong", r_user1_still_ok.status_code == 200 and r_user1_still_ok.json().get("ok"))

        # ------------------------------------------------------------
        # 7. Kiem tra co lap Store (SessionStore & TaskStore) theo User
        # ------------------------------------------------------------
        import sessions
        # Dat ContextVar cho user1
        c.current_user_id.set(uid1)
        store1 = sessions.get_store()
        check("store1 tro vao dung db cua user1", str(dir1) in str(store1.db_path))
        s1_id = store1.create_session(title="Chat cua user1", brain="brain")
        store1.append_message(s1_id, "user", "Tin nhan bi mat cua user1")

        # Chuyen ContextVar sang user2
        c.current_user_id.set(uid2)
        store2 = sessions.get_store()
        check("store2 tro vao dung db cua user2", str(dir2) in str(store2.db_path))
        check("hai store cua 2 user doc lap", store1 != store2)
        check("user2 khong thay session cua user1", store2.get_session(s1_id) is None)

        s2_id = store2.create_session(title="Chat cua user2", brain="brain")
        store2.append_message(s2_id, "user", "Tin nhan rieng cua user2")
        check("user1 khong thay session cua user2", store1.get_session(s2_id) is None)

        # Kiem tra TasksFeature.store (Kanban)
        c.current_user_id.set(uid1)
        t_store1 = main.tasks_feature.store
        check("task store 1 tro vao dung dir1", str(dir1) in str(t_store1.path))
        tid1 = t_store1.enqueue("brain", "Task cua user1", "Noi dung task 1")

        c.current_user_id.set(uid2)
        t_store2 = main.tasks_feature.store
        check("task store 2 tro vao dung dir2", str(dir2) in str(t_store2.path))
        check("hai task store doc lap", t_store1 != t_store2)
        check("user2 khong thay task cua user1", t_store2.get_task(tid1) is None)

        tid2 = t_store2.enqueue("brain", "Task cua user2", "Noi dung task 2")
        check("user1 khong thay task cua user2", t_store1.get_task(tid2) is None)

        # Kiem tra API /sessions cach ly theo session cookie cua tung user
        s_list_u1 = client.get("/sessions", cookies={"javis_session": tok1}).json()
        u1_titles = [s.get("title") for s in s_list_u1.get("sessions", [])]
        check("API /sessions user1 chua phien cua user1", "Chat cua user1" in u1_titles)
        check("API /sessions user1 KHONG chua phien cua user2", "Chat cua user2" not in u1_titles)

        s_list_u2 = client.get("/sessions", cookies={"javis_session": tok_alice_v3}).json()
        u2_titles = [s.get("title") for s in s_list_u2.get("sessions", [])]
        check("API /sessions user2 chua phien cua user2", "Chat cua user2" in u2_titles)
        check("API /sessions user2 KHONG chua phien cua user1", "Chat cua user1" not in u2_titles)

        # ------------------------------------------------------------
        # 8. Kiem tra co lap settings.json theo User
        # ------------------------------------------------------------
        c.current_user_id.set(uid1)
        cfg1 = c.read_settings()
        cfg1["workspace_name"] = "Workspace of User 1"
        c.write_settings(cfg1)

        c.current_user_id.set(uid2)
        cfg2 = c.read_settings()
        cfg2["workspace_name"] = "Workspace of User 2"
        c.write_settings(cfg2)

        p_set1 = c.get_settings_path(uid1)
        p_set2 = c.get_settings_path(uid2)
        check("settings user1 luu tai dir1", str(dir1) in str(p_set1))
        check("settings user2 luu tai dir2", str(dir2) in str(p_set2))

        c.current_user_id.set(uid1)
        check("user1 doc dung workspace_name user1", c.read_settings().get("workspace_name") == "Workspace of User 1")

        c.current_user_id.set(uid2)
        check("user2 doc dung workspace_name user2", c.read_settings().get("workspace_name") == "Workspace of User 2")

        # ------------------------------------------------------------
        # 9. Kiem tra co lap customer_conversations.sqlite3 theo User
        # ------------------------------------------------------------
        import conversations
        c.current_user_id.set(uid1)
        p_conv1 = conversations.get_db_path()
        check("conversations user1 tro vao dir1", str(dir1) in str(p_conv1))
        res1 = conversations.ghi_su_kien({
            "channel": "telegram",
            "account_id": "bot_1",
            "external_chat_id": "chat_u1",
            "sender_type": "customer",
            "sender_id": "cust_1",
            "sender_name": "Khach User 1",
            "text": "Hello user 1",
        })
        c1_id = res1.get("conversation_id")
        check("ghi su kien user 1 thanh cong", bool(c1_id))

        c.current_user_id.set(uid2)
        p_conv2 = conversations.get_db_path()
        check("conversations user2 tro vao dir2", str(dir2) in str(p_conv2))
        check("cuoc chat cua bot 1 khong ton tai o user2", len(conversations.cuoc_chat_cua_tai_khoan("telegram", "bot_1")) == 0)
        check("danh sach hoi thoai user2 rong", len(conversations.danh_sach()) == 0)

        res2 = conversations.ghi_su_kien({
            "channel": "telegram",
            "account_id": "bot_2",
            "external_chat_id": "chat_u2",
            "sender_type": "customer",
            "sender_id": "cust_2",
            "sender_name": "Khach User 2",
            "text": "Hello user 2",
        })
        c2_id = res2.get("conversation_id")
        check("ghi su kien user 2 thanh cong", bool(c2_id))
        conv_u2 = conversations.chi_tiet(c2_id)
        check("hoi thoai cua user2 mang dung chat_u2", (conv_u2 or {}).get("external_chat_id") == "chat_u2")

        c.current_user_id.set(uid1)
        conv_u1 = conversations.chi_tiet(c1_id)
        check("hoi thoai cua user1 mang dung chat_u1", (conv_u1 or {}).get("external_chat_id") == "chat_u1")
        check("cuoc chat cua bot 2 khong ton tai o user1", len(conversations.cuoc_chat_cua_tai_khoan("telegram", "bot_2")) == 0)

        # ------------------------------------------------------------
        # 10. Kiem tra co lap runtime.db theo User
        # ------------------------------------------------------------
        import context_runtime
        c.current_user_id.set(uid1)
        rt = context_runtime.get_runtime()
        trace1 = rt.start_turn("sess-1", "brain", "dashboard")
        rt.record_runtime_event(trace1, "custom.event.u1", {"hello": "u1"})
        p_rt1 = dir1 / "runtime.db"
        check("runtime.db user1 duoc tao o dir1", p_rt1.exists())

        c.current_user_id.set(uid2)
        trace2 = rt.start_turn("sess-2", "brain", "dashboard")
        rt.record_runtime_event(trace2, "custom.event.u2", {"hello": "u2"})
        p_rt2 = dir2 / "runtime.db"
        check("runtime.db user2 duoc tao o dir2", p_rt2.exists())
        check("user2 khong thay task cua user1 trong runtime", rt.get_task(trace1.task_id) is None)

        # ------------------------------------------------------------
        # 11. Kiem tra co lap login_vault.sqlite3 va web_watch.sqlite3 theo User
        # ------------------------------------------------------------
        c.current_user_id.set(uid1)
        lv_store1 = main.login_vault.LoginVault(main.login_vault.LoginVaultDeps(
            state_dir=lambda: c.get_user_state_dir(),
            has_session=lambda r: True,
            fetch=lambda *a, **k: None,
        )).store
        check("login_vault store1 nam trong dir1", str(dir1) in str(lv_store1.path))
        lv_store1.add({"id": "lid1", "name": "Login 1", "cookies_enc": "enc:test", "domains": "[]", "created_at": 1, "updated_at": 1})

        c.current_user_id.set(uid2)
        lv_store2 = main.login_vault.LoginVault(main.login_vault.LoginVaultDeps(
            state_dir=lambda: c.get_user_state_dir(),
            has_session=lambda r: True,
            fetch=lambda *a, **k: None,
        )).store
        check("login_vault store2 nam trong dir2", str(dir2) in str(lv_store2.path))
        check("user2 khong thay cookie cua user1", lv_store2.get("lid1") is None)

        c.current_user_id.set(uid1)
        check("watch_feature store user1 tro vao dir1", str(dir1) in str(main.watch_feature.store.path))

        c.current_user_id.set(uid2)
        check("watch_feature store user2 tro vao dir2", str(dir2) in str(main.watch_feature.store.path))

        # ------------------------------------------------------------
        # 12. Kiem tra co lap github_work.db theo User
        # ------------------------------------------------------------
        import github_store
        c.current_user_id.set(uid1)
        gh_store1 = github_store.store()
        check("github store 1 tro vao dir1", str(dir1) in str(gh_store1.path))
        gh_store1.ensure("sid1", "owner/repo1", branch="feature-1")

        c.current_user_id.set(uid2)
        gh_store2 = github_store.store()
        check("github store 2 tro vao dir2", str(dir2) in str(gh_store2.path))
        check("user2 khong thay github work cua user1", gh_store2.get("sid1", "owner/repo1") is None)

        c.current_user_id.set(uid1)
        check("user1 thay github work cua minh", gh_store1.get("sid1", "owner/repo1") is not None)

    finally:
        shutil.rmtree(test_state_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    run_tests()
    print()
    if _fails:
        print(f"FAIL {len(_fails)} test: " + ", ".join(_fails))
        sys.exit(1)
    print("TAT CA TEST MULTI-USER PASS")

