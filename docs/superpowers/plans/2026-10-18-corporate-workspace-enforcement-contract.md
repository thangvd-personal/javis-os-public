# Corporate Workspace Enforcement Contract Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cập nhật hợp đồng JavisOS-IAM-MCP v0.3 để G2 là Corporate Workspace Enforcement Pilot, G3 là Controlled Business Actions và G4 là Enterprise Scale-out.

**Architecture:** Chỉ sửa tài liệu hợp đồng hiện có. Giữ nguyên các bất biến kỹ thuật và gate G0; diễn đạt lại thứ tự, phạm vi, điều kiện mở/đóng và giới hạn nghiệp vụ theo roadmap mới, không tuyên bố rằng bằng chứng triển khai đã được nghiệm thu.

**Tech Stack:** Markdown, PowerShell text checks.

**Spec:** `javisos/brains/SME-BTOS/documents/hop-dong-tich-hop-javisos-iam-mcp-v0.3.md` và đề xuất Corporate Workspace Enforcement do người dùng cung cấp ngày 2026-10-18.

## Global Constraints
- G0 chỉ đóng khi toàn bộ điều kiện tại Mục 12 có bằng chứng và phê duyệt thực tế.
- G0, G1 và G2 không tự động cấp quyền mở gate tiếp theo.
- G2 không cho phép ghi/sửa/xóa/publish/giao dịch nghiệp vụ hoặc chia sẻ Corporate sang Personal.
- Mọi quyền tiếp tục được đánh giá theo từng `resource × operation`; thiếu context hoặc không xác minh được chính sách thì từ chối.

## Review Focus
1. Roadmap mới phải nhất quán xuyên suốt tài liệu: G1 Identity & Read-only, G2 Corporate Workspace Enforcement, G3 Controlled Business Actions, G4 Enterprise Scale-out.
2. Không làm yếu yêu cầu fail-closed, revoke, workspace isolation, lineage, audit hoặc quyền downstream.
3. G2 phải mô tả khả năng người dùng sử dụng AI với dữ liệu Corporate được duyệt nhưng không biến thành quyền thực hiện nghiệp vụ ghi.
4. Các mã gate cũ `G2-ACT-*`, `G2-SHARE-*`, `G2-DLP-*` phải được phân loại lại cho G3 và nêu yêu cầu đồng bộ register, tránh tạo ấn tượng bảng truy vết đã được cập nhật.
5. Tài liệu phải phân biệt trạng thái thiết kế/ma trận PASS với quyết định chính thức cho phép pilot.

---

### Task 1: Rewrite the v0.3 phase contract

**Files:**
- Modify: `javisos/brains/SME-BTOS/documents/hop-dong-tich-hop-javisos-iam-mcp-v0.3.md`

**Interfaces:**
- Consumes: Existing G0 contract, G1 read-only requirements, G2 action gate IDs, and approved Corporate Workspace Enforcement proposal.
- Produces: A consistent G0-G4 roadmap and G2 acceptance scope in the existing contract.

- [x] **Step 1: Record the current phase references and validate they exhibit the old roadmap**

Run: `rg -n "G2-ACT|G2-SHARE|G2-DLP|G1 Corporate read-only|G2 và thay đổi có tác động" javisos/brains/SME-BTOS/documents/hop-dong-tich-hop-javisos-iam-mcp-v0.3.md`
Expected: The old G2 action/share/egress wording and IDs appear; no G4 roadmap exists.

- [x] **Step 2: Rewrite the introduction, phase sections, open decisions, trace mapping, and conclusion**

Preserve the existing G0 security contract. Define G1 as Identity & Read-only; G2 as a bounded Corporate Workspace Enforcement Pilot with operation-level enforcement, isolation, revocation, lineage, DLP, audit, and runtime evidence; G3 as per-action controlled business changes; and G4 as scale-out. State that G2 enables no business write action or Corporate-to-Personal sharing. Reclassify the existing `G2-ACT-*`, `G2-SHARE-*`, and `G2-DLP-*` gates as G3 prerequisites, and explicitly require a change record and register synchronization before acceptance.

- [x] **Step 3: Verify the roadmap, exclusions, references, and Markdown structure**

Run a PowerShell assertion script against the edited contract. Expected: all four phase names are present; the G2 exclusions and G3 action gate IDs are present; the old statement that G2 is the action gate is absent; all 18 numbered sections remain; the G0 register link resolves; exit code 0.

### Task 2: Resolve review findings in the v0.3 contract

**Files:**
- Modify: `javisos/brains/SME-BTOS/documents/hop-dong-tich-hop-javisos-iam-mcp-v0.3.md`

**Interfaces:**
- Consumes: G0 acceptance matrix, G1 acceptance matrix/report, and G2 data lifecycle invariants from sections 4, 8, 9, and 14.
- Produces: Explicit G0-025 closure trace, separate reported G1 technical status and formal approval status, and Corporate prompt/output lifecycle rules.

- [x] **Step 1: Reproduce the review gaps in the current contract**

Run: `rg -n "G0-001.*G0-024|Bản v0.2|G1-001..G1-018|output|prompt" javisos/brains/SME-BTOS/documents/hop-dong-tich-hop-javisos-iam-mcp-v0.3.md`
Expected: G0 closure stops at G0-024, the stale v0.2 sentence remains, and G1 status/output lifecycle is not explicit.

- [x] **Step 2: Correct G0 scope, document G1 status evidence, and specify G2 derived-data lifecycle**

Update G0 closure to cover G0-001 through G0-025 and explicitly trace G0-025 to its architecture decision and CT-SUB-001 evidence. Record G1-001..018 as PASS only as reported by the current matrix/report while keeping formal gate approval unverified/not approved until a signed decision is linked. Replace the v0.2 wording. Define default transient-only prompt/history/output handling, retention, reopen behavior, source classification inheritance, and revoke cleanup, consistent with sections 4.3, 8, 9, and the G2 write restrictions.

- [x] **Step 3: Verify the review fixes and consistency**

Run a PowerShell assertion script. Expected: G0-025 is included and traced; no stale v0.2 claim remains; G1 technical PASS and formal approval are separate; G2 content is transient by default with revoke and retention behavior stated; 18 numbered sections and local links remain valid; exit code 0.

### Task 3: Create the G2 Corporate Workspace Enforcement acceptance matrix

**Files:**
- Create: `javisos/brains/SME-BTOS/documents/g2-pilot/g2-cwe-acceptance-matrix.md`
- Modify: `javisos/brains/SME-BTOS/documents/hop-dong-tich-hop-javisos-iam-mcp-v0.3.md`

**Interfaces:**
- Consumes: G2 scope and exclusions from contract sections 14.1-14.4; G0/G1 evidence as candidate coverage only.
- Produces: A G2-CWE register with measurable PASS criteria, required evidence/reviewer fields, and initial status `CHƯA XÁC MINH` for every row.

- [x] **Step 1: Define G2-only criteria and preserve the gate status**

Include corporate identity/context, Personal-Corporate separation, same-tenant cross-employee workspace isolation, operation-level denials for deferred business actions, model/classification egress, Gateway bypass, revoke/fail-closed, derived-data lineage and retention, audit access/integrity, prompt injection, and incident controls. State `PREPARATION APPROVED — NOT OPEN FOR OPERATION`; existing G0/G1 evidence is candidate coverage only and is not G2 evidence without a G2-scoped run and review.

- [x] **Step 2: Add the matrix and link it from contract section 14**

Each row must have a unique `G2-CWE-*` ID, contract/control trace, measurable PASS criteria, applicable code/test references or an explicit coverage gap, required evidence fields, reviewer, and status `CHƯA XÁC MINH`. Explicitly defer `write`, `delete`, `publish`, transaction, `export`, and Corporate-to-Personal sharing to a later gate.

- [x] **Step 3: Verify structure, scope, links, IDs, and statuses**

Run PowerShell assertions against the matrix and contract. Expected: IDs are unique; every row starts `CHƯA XÁC MINH`; required coverage and deferred-operation exclusions are present; local references resolve; no row claims PASS; contract still states G2 is not open for operation.

### Task 4: Prepare the G2 test and evidence package

**Files:**
- Modify: `javisos/brains/SME-BTOS/documents/g2-pilot/g2-cwe-acceptance-matrix.md`
- Create: `javisos/brains/SME-BTOS/documents/g2-pilot/g2-test-case-register.md`
- Create: `javisos/brains/SME-BTOS/documents/g2-pilot/g2-traceability-register.md`
- Create: `javisos/brains/SME-BTOS/documents/g2-pilot/g2-test-execution-evidence-plan.md`

**Interfaces:**
- Consumes: The 19 G2-CWE criteria, contract v0.3, G0/G1 decisions and evidence references, IAM/PDP semantics and schemas.
- Produces: Prepared test scenarios, requirement-to-evidence traceability, a future execution/evidence protocol, and unresolved pilot decisions; no executable G2 code or actual test results.

- [x] **Step 1: Separate preparation status from verification status**

Add a `Preparation Status` field without changing any G2-CWE verification status. Use only the agreed preparation vocabulary and leave items blocked by missing pilot decisions as `CHƯA CHUẨN BỊ` or `ĐANG CHUẨN BỊ`; do not mark a case `SẴN SÀNG KIỂM THỬ` absent an approved scope and executable prerequisites.

- [x] **Step 2: Build the test case register**

Assign stable test case IDs; for each case record linked G2-CWE IDs, prerequisites, synthetic/canary data, planned steps, expected allow/deny/failure behavior, downstream verification, evidence to capture, and preparation status. Group by P0 isolation/bypass, P1 authorization/lifecycle, and P2 audit/governance/incident response.

- [x] **Step 3: Build traceability and execution/evidence plans**

Map requirement -> contract/architecture/API/schema/decision -> G2-CWE -> test case -> planned evidence -> future evaluation. Record pilot profile values as `CẦN CHỐT` with owner; define future environment controls, test-data rules, run records, artifact handling, failure triage, independent review, readiness and operational approval as separate gates.

- [x] **Step 4: Verify package consistency and unresolved inputs**

Run PowerShell assertions for unique test IDs, all 19 criteria traced, valid document/schema/API references, separated status vocabularies, no fabricated actual results, and preserved G0/G1/G2 decisions. Expected: no test marked ready where a prerequisite remains `CẦN CHỐT`; G2 remains closed to operation.

