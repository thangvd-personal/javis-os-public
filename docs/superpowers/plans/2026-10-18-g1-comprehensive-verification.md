# G1 Comprehensive Live Verification & Security Test Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Triển khai bộ kiểm thử xác thực thực tế cấp độ 1, 2 và 3 cho G1 Pilot (P0 & P1), bao gồm End-to-End SSO thật (không mock token exchange hops), Authorization Code single-use & expiration security, Emergency Halt trên Gateway thật, Audit Outbox Tamper Detection, và Real Execution Test Runner trong Assurance Console.

**Architecture:**
- Loại bỏ hoàn toàn mock khỏi các chặng đổi token trong kịch bản tích hợp thật: kết nối trực tiếp từ JavisOS sang container `sme_backend` trên `localhost:8000`.
- Bổ sung ma trận kiểm thử an toàn SSO đa chiều: tái sử dụng code, hết hạn 60s, sai lệch client credentials, concurrency race-condition, và state nonce replay.
- Kiểm thử Emergency Halt trên gateway thật: xác thực việc thu hồi quyền hoặc đình chỉ tenant chặn đứng request kế tiếp với reason code chuẩn xác và ghi audit log.
- Kiểm thử khả năng phát hiện sửa đổi (tamper detection) trên SQLite Audit Outbox.
- Nâng cấp test runner trong Assurance Console từ trạng thái stub `TRIGGERED` thành thực thi kiểm thử thật và trả về kết quả chi tiết kèm bằng chứng.

**Tech Stack:** Python 3.12, FastAPI, Django REST Framework, Docker, SQLite, pytest, urllib.request, threading.

**Spec:** `javisos/brains/SME-BTOS/documents/g1-pilot/README.md` & `Standard IAM/PDP Contract v1.0`.

## Global Constraints
- Standard IAM/PDP Contract v1.0 giữ nguyên tính bất biến (G0 Contract Immutability).
- Toàn bộ token (Session Token, Execution Token) lưu trữ độc quyền ở server-side, tuyệt đối không lộ qua URL, HTML, hoặc cookie browser.
- Emergency Halt và Revocation phải được kiểm chứng bằng request thực tế qua McpGatewayPepService, trả đúng HTTP status, reason code và correlation ID.

## Review Focus
1. **Mock bypass:** Đảm bảo test tích hợp thực sự gửi HTTP request tới `localhost:8000` và nhận response thật từ Django, không sử dụng `unittest.mock.patch` cho `_exchange_sso_code_for_session_token` hay `_exchange_session_token_for_execution_token`.
2. **Race Condition trên Auth Code:** Hai request đồng thời đổi cùng một code phải được khóa nguyên tử (atomic consumption); chỉ 1 request thành công, request còn lại phải bị từ chối 400.
3. **Audit Hash Tampering:** Khi sửa đổi bất kỳ byte nào của record đã lưu trong SQLite outbox, `verify_integrity()` phải trả về `valid: False` và chỉ đúng số thứ tự bản ghi bị can thiệp.
4. **Emergency Halt Response:** Request bị từ chối bởi Emergency Halt phải mang reason code `PERMISSION_REVOKED` hoặc `TENANT_SUSPENDED`, không được trả lỗi chung chung.
5. **Assurance Console Real Execution:** Endpoint `/api/run-test` phải thực sự chạy kịch bản kiểm thử, trả về `PASSED`/`FAILED`, thời gian thực thi `duration_ms` và bằng chứng chi tiết.

---

### Task 1: P0 — End-to-End Live SSO Integration Test (`SSO-INT-001`)

**Files:**
- Create: `javisos/tests/contract/test_g1_sso_live_integration.py`
- Test: `javisos/tests/contract/test_g1_sso_live_integration.py`

- [ ] **Step 1: Viết test case `SSO-INT-001` kết nối trực tiếp với `localhost:8000`**
  - Khởi tạo SSO qua `/auth/sme-sso/start` nhận `state` và `redirect_url`.
  - Thực hiện request POST tới `http://localhost:8000/api/v1/iam/sso/authorize` với tài khoản `alice_corp` / `AliceCorp@2026`.
  - Nhận `code` từ URL chuyển hướng Location header.
  - Gửi GET tới `/auth/sme-callback?code=...&state=...` trên JavisOS mà KHÔNG mock token exchange functions.
  - Xác nhận JavisOS thực hiện thật 2 chặng: Hop 1 sang `http://localhost:8000/api/v1/iam/sso/token` và Hop 2 sang `http://localhost:8000/api/v1/iam-pdp-contract/v1/tokens/exchange`.
  - Xác nhận `sme_session_id` được thiết lập trong cookie của JavisOS.
  - Lấy `execution_token` từ session store server-side và gọi McpGatewayPepService đọc dữ liệu `res_worker_01` thành công.
  - Xác nhận không có bất kỳ token nào xuất hiện trong URL, HTML body, hoặc cookie value.

- [ ] **Step 2: Chạy test kiểm chứng**
  - Lệnh: `pytest javisos/tests/contract/test_g1_sso_live_integration.py -v`
  - Kết quả kỳ vọng: 1 passed.

- [ ] **Step 3: Commit**
  - Lệnh: `git add tests/contract/test_g1_sso_live_integration.py && git commit -m "test(sso): add SSO-INT-001 unmocked live end-to-end integration test"`

---

### Task 2: P0 — Authorization Code & Session Security Matrix (`SSO-SEC-002` & `SSO-SEC-003`)

**Files:**
- Create: `javisos/tests/contract/test_g1_sso_security_matrix.py`
- Test: `javisos/tests/contract/test_g1_sso_security_matrix.py`

- [ ] **Step 1: Viết test cases kiểm thử bảo mật Authorization Code (`SSO-SEC-002`)**
  - Case 1: Tái sử dụng code (Replay Attack) -> Lần 1 thành công (200), Lần 2 bị từ chối 400 (`CODE_ALREADY_CONSUMED` hoặc `INVALID_CODE`).
  - Case 2: Code quá hạn 60 giây -> Bị từ chối 400 (`CODE_EXPIRED`).
  - Case 3: Client ID sai hoặc thiếu Secret -> Bị từ chối 401 (`INVALID_CLIENT_CREDENTIALS`).
  - Case 4: `redirect_uri` không khớp -> Bị từ chối 400 (`INVALID_REDIRECT_URI`).
  - Case 5: Race Condition / Concurrency test -> Gửi đồng thời 5 request đổi cùng 1 code qua thread pool -> Đúng 1 request thành công (200), 4 request còn lại thất bại (400).

- [ ] **Step 2: Viết test cases kiểm thử bảo mật Phiên & Cookie (`SSO-SEC-003`)**
  - Case 1: Callback với `state` không tồn tại hoặc đã qua 300s -> Bị từ chối 400 (`STATE_MISMATCH_OR_EXPIRED`).
  - Case 2: Callback với `state` đã dùng một lần (Replay state) -> Bị từ chối 400 ở lần 2.
  - Case 3: Cookie `sme_session_id` và `javis_session` mang đầy đủ cờ `httponly=True`, `samesite=lax`, `path=/`.
  - Case 4: Giá trị session cookie là chuỗi ngẫu nhiên opaque `sme_sess_...`, không chứa và không thể giải mã ra token bí mật.

- [ ] **Step 3: Chạy test kiểm chứng**
  - Lệnh: `pytest javisos/tests/contract/test_g1_sso_security_matrix.py -v`
  - Kết quả kỳ vọng: Tất cả các security test cases đều PASS.

- [ ] **Step 4: Commit**
  - Lệnh: `git add tests/contract/test_g1_sso_security_matrix.py && git commit -m "test(sso): add SSO-SEC-002 and SSO-SEC-003 code & session security matrix"`

---

### Task 3: P0 — Live Gateway Emergency Halt & Revocation Enforcement (`G1-018-LIVE`)

**Files:**
- Create: `javisos/tests/contract/test_g1_emergency_halt_live.py`
- Modify: `javisos/server/mcp_gateway/pep_service.py` (nếu cần tinh chỉnh reason code mapping)
- Test: `javisos/tests/contract/test_g1_emergency_halt_live.py`

- [ ] **Step 1: Viết test case `G1-018-LIVE` trên Gateway thật**
  - Tạo phiên hợp lệ với `execution_token` được cấp từ SME_BTOS.
  - Gửi request đọc hợp lệ qua `McpGatewayPepService` -> Được phép (`ALLOW`, reason `OK_GRANTED`).
  - Kích hoạt Emergency Halt / Revocation: Tăng `permission_version` của tenant trong SME_BTOS Redis / Postgres hoặc adapter state.
  - Gửi tiếp request đọc thứ 2 với cùng `execution_token` đó.
  - Xác nhận Gateway từ chối request với exception `GatewayPepError` hoặc HTTP 403 mang reason code chính xác `PERMISSION_REVOKED` hoặc `TENANT_SUSPENDED`.
  - Xác nhận response trả về mang `correlation_id` xuyên suốt.
  - Xác nhận `DurableAuditOutbox` ghi nhận sự kiện `DENY` với đúng reason code và payload hash.

- [ ] **Step 2: Chạy test kiểm chứng**
  - Lệnh: `pytest javisos/tests/contract/test_g1_emergency_halt_live.py -v`
  - Kết quả kỳ vọng: 1 passed.

- [ ] **Step 3: Commit**
  - Lệnh: `git add tests/contract/test_g1_emergency_halt_live.py && git commit -m "test(gateway): add G1-018-LIVE emergency halt and mid-flight revocation verification"`

---

### Task 4: P1 — Audit Outbox Tamper Detection Test (`G1-AUDIT-007`)

**Files:**
- Create: `javisos/tests/contract/test_g1_audit_tamper_detection.py`
- Test: `javisos/tests/contract/test_g1_audit_tamper_detection.py`

- [ ] **Step 1: Viết test case cố ý sửa đổi dữ liệu audit để kiểm chứng khả năng phát hiện**
  - Tạo outbox SQLite và ghi 5 sự kiện audit liên tiếp với chuỗi hash hoàn chỉnh (`prev_event_hash -> record_hash`).
  - Kiểm tra ban đầu: `outbox.verify_integrity()` trả về `valid: True`, `broken_seq: None`.
  - Mở trực tiếp kết nối SQLite can thiệp chỉnh sửa dữ liệu:
    - Thay đổi trường `operation` hoặc `payload_hash` ở record `sequence_number = 3`.
  - Gọi lại `outbox.verify_integrity()`:
    - Xác nhận trả về `valid: False`.
    - Xác nhận chỉ ra đúng `broken_seq: 3` hoặc `broken_seq: 4` (do lệch `prev_event_hash`).
    - Xác nhận nguyên nhân `HASH_MISMATCH` hoặc `CHAIN_BROKEN`.
  - Thử xóa hẳn record `sequence_number = 3`:
    - Xác nhận phát hiện đứt gãy thứ tự chuỗi (`SEQUENCE_GAP`).

- [ ] **Step 2: Chạy test kiểm chứng**
  - Lệnh: `pytest javisos/tests/contract/test_g1_audit_tamper_detection.py -v`
  - Kết quả kỳ vọng: 1 passed.

- [ ] **Step 3: Commit**
  - Lệnh: `git add tests/contract/test_g1_audit_tamper_detection.py && git commit -m "test(audit): add G1-AUDIT-007 tamper detection and hash chain verification"`

---

### Task 5: P1 — Assurance Console Real Test Execution & Detailed Reporting (`G1-CONSOLE-008`)

**Files:**
- Modify: `javisos/brains/SME-BTOS/documents/g1-pilot/assurance-console/server.py`
- Modify: `javisos/brains/SME-BTOS/documents/g1-pilot/assurance-console/app.js`
- Modify: `javisos/tests/contract/test_g1_assurance_console_contract.py`
- Test: `javisos/tests/contract/test_g1_assurance_console_contract.py`

- [ ] **Step 1: Cập nhật `server.py` của Assurance Console để thực thi test thật**
  - Trong handler `run_test`:
    - Với `TEST_A`: Chạy kịch bản kiểm tra Revocation / Permission Bump và xác nhận `PERMISSION_REVOKED`.
    - Với `TEST_B`: Chạy kịch bản Prompt Guard & Retention Evaluator cho model cấm.
    - Với `TEST_C`: Chạy kịch bản Fail-Closed khi timeout hoặc mất heartbeat.
  - Trả về payload chi tiết:
    ```json
    {
      "test_name": "TEST_A",
      "status": "PASSED",
      "duration_ms": 45,
      "executed_at": 1792289900.0,
      "assertions_passed": 3,
      "evidence": {
        "reason_code": "PERMISSION_REVOKED",
        "hash_verified": true
      }
    }
    ```
  - Ghi bản ghi audit tương ứng vào SQLite outbox để Auditor xem được ngay.

- [ ] **Step 2: Cập nhật giao diện `app.js` của Console**
  - Hiển thị badge kết quả: `PASSED` (xanh) / `FAILED` (đỏ), thời gian chạy `duration_ms` và tóm tắt assertion.
  - Tự động gọi `fetchAuditEvents()` để làm mới bảng sự kiện sau khi test chạy xong.

- [ ] **Step 3: Cập nhật test contract `test_g1_assurance_console_contract.py`**
  - Xác nhận `status == "PASSED"`, có `duration_ms`, `evidence`, và không còn nhận giá trị stub `TRIGGERED`.

- [ ] **Step 4: Chạy test kiểm chứng**
  - Lệnh: `pytest javisos/tests/contract/test_g1_assurance_console_contract.py -v`
  - Kết quả kỳ vọng: Tất cả passed.

- [ ] **Step 5: Commit**
  - Lệnh: `git add brains/SME-BTOS/documents/g1-pilot/assurance-console/server.py brains/SME-BTOS/documents/g1-pilot/assurance-console/app.js tests/contract/test_g1_assurance_console_contract.py && git commit -m "feat(console): implement real test execution and evidence reporting for G1-CONSOLE-008"`

---

### Task 6: P1 — Read-Only & IDOR Enforcement Across APIs (`G1-READ-001`, `G1-RO-002`, `G1-TENANT-005`)

**Files:**
- Create: `javisos/tests/contract/test_g1_policy_enforcement.py`
- Test: `javisos/tests/contract/test_g1_policy_enforcement.py`

- [ ] **Step 1: Viết test case `G1-READ-001` & `G1-RO-002`**
  - Đọc tài nguyên `worker_profile` đã cấp quyền -> 200 OK.
  - Thử các thao tác bị cấm: `write`, `delete`, `download`, `export`, `share` qua PEP -> Bị chặn 100% với reason code `READ_ONLY_VIOLATION` hoặc `OPERATION_FORBIDDEN`.
  - Thử truy cập tài nguyên ngoài tenant hoặc sai workspace (`G1-TENANT-005`) -> Bị từ chối với reason code `CROSS_TENANT_FORBIDDEN` hoặc `IDOR_RESOURCE_MISMATCH`.

- [ ] **Step 2: Chạy test kiểm chứng**
  - Lệnh: `pytest javisos/tests/contract/test_g1_policy_enforcement.py -v`
  - Kết quả kỳ vọng: Passed.

- [ ] **Step 3: Commit**
  - Lệnh: `git add tests/contract/test_g1_policy_enforcement.py && git commit -m "test(policy): add G1-READ-001, G1-RO-002, and G1-TENANT-005 policy enforcement tests"`

---

### Task 7: Tổng Hợp & Cập Nhật Báo Cáo Nghiệm Thu 3 Cấp Độ

**Files:**
- Modify: `javisos/brains/SME-BTOS/documents/g1-pilot/g1-acceptance-matrix.md`
- Modify: `javisos/brains/SME-BTOS/documents/g1-pilot/g1-pilot-report.md`

- [ ] **Step 1: Chạy toàn bộ test suite từ gốc để thu thập bằng chứng 100% xanh**
  - Lệnh: `$env:PYTHONPATH="d:\btos_javis;d:\btos_javis\javisos\server"; d:\btos_javis\javisos\.venv\Scripts\python.exe -m pytest tests/contract/ -v`

- [ ] **Step 2: Cập nhật Ma Trận Nghiệm Thu phân định 3 cấp độ**
  - Level 1: **SSO PASS** (UX & Login thật)
  - Level 2: **SSO Security PASS** (One-time code, zero token leak, CSRF protection, session integrity)
  - Level 3: **G1 ACCEPTED** (Live read allowed, prohibited ops blocked, live emergency halt, tamper-evident audit, real assurance console execution)

- [ ] **Step 3: Commit**
  - Lệnh: `git add brains/SME-BTOS/documents/g1-pilot/g1-acceptance-matrix.md brains/SME-BTOS/documents/g1-pilot/g1-pilot-report.md && git commit -m "docs(g1): update acceptance matrix and pilot report with 3-level assurance criteria and live evidence"`
