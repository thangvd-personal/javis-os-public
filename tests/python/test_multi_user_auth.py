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

        check("duong dan state user1 chua user_id1", uid1 in str(dir1))
        check("duong dan state user2 chua user_id2", uid2 in str(dir2))
        check("hai thu muc state phai khac nhau hoan toan", dir1 != dir2)

        # Kiem tra quy uoc duong dan mac dinh server/state/[user_id]
        server_dir = c.Path(c.__file__).parent.resolve()
        expected_user_dir = server_dir / "state" / uid1
        check("get_user_state_dir theo mac dinh nam trong server/state/[user_id]", expected_user_dir.name == uid1 and expected_user_dir.parent.name == "state")

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

        # User1 van dang nhap binh thuong voi pw1
        r_user1_still_ok = client.post("/auth/login", data={"username": "thangvd", "password": pw1})
        check("user1 van dang nhap binh thuong", r_user1_still_ok.status_code == 200 and r_user1_still_ok.json().get("ok"))

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

