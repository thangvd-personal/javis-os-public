# Goal triển khai cải tiến agent và skill Javis, M0–M8

Ngày: 22/10/2026, Asia/Saigon.

Tài liệu này là chỉ dẫn cho một Goal sẽ được chủ phát lệnh. Việc soạn/lưu tài liệu không khởi động Goal, không gọi model thử nghiệm và không cấp quyền triển khai production. Phần ủy quyền bên dưới có hiệu lực khi chủ gửi lệnh Goal tham chiếu toàn bộ file này.

## 1. Một lệnh để khởi động

```text
/goal Hoàn thành cải tiến agent và skill Javis từ M0 đến M8 theo toàn bộ chỉ dẫn tại D:\Project\Javis-OS\docs\superpowers\AGENT-SKILL-IMPROVEMENT-GOAL.md. Tự lập và thực hiện kế hoạch, kiểm thử, review, sửa lỗi, tạo PR và tích hợp theo quy ước repo; tiếp tục qua các mốc đủ điều kiện mà không hỏi lại xác nhận thông thường. Chỉ hoàn thành khi có bằng chứng đạt đầy đủ các điều kiện trong file; nếu còn gate nghiệp vụ, dữ liệu, quyền hoặc thời gian quan sát thật chưa đạt thì báo chính xác phần bị chặn và tiếp tục mọi phần độc lập còn làm được.
```

Đây là lệnh nhập trong Codex hỗ trợ `/goal`, không phải lệnh chạy PowerShell. Chỉ cần phát một lần; mọi lần tiếp tục phải đọc hồ sơ tiến độ và làm tiếp từ điểm chưa hoàn tất.

## 2. Mục tiêu xuyên suốt

Đưa chức năng **Cải tiến có kiểm chứng** của Javis từ spec thành sản phẩm thực thi được, bao gồm toàn bộ M0–M8: nền revision/guard/nghiệm thu, sandbox và case, thử sửa skill có đối chứng, qualification/canary/rollback, skill routing và adapter engine, cải tiến agent có phạm vi, bundle agent–skill–helper, Team và thư viện kinh nghiệm thích nghi.

Không kết thúc ở viết kế hoạch, demo giao diện, scaffold module, test giả hoặc chỉ MVP M0–M3. Hoàn thành kỹ thuật, hiệu quả nghiệp vụ và đủ điều kiện rollout là ba loại bằng chứng khác nhau, phải theo dõi riêng cho từng mốc.

Nguồn chuẩn:

1. [Spec agent/skill M0–M8](specs/2026-10-22-agent-skill-evidence-improvement-design.md), toàn bộ 29 mục.
2. [Spec nghiệm thu và usage Team](specs/2026-10-22-team-quality-first-design.md), chỉ những phụ thuộc cần cho mốc đang làm.
3. `AGENTS.md` nếu có, `CLAUDE.md`/`AGENT.md` áp dụng, `ARCHITECTURE.md` trên main mới và `docs/quy-uoc-dev.md`.
4. Mã nguồn hiện tại và bằng chứng test/pilot thật. Tài liệu tên hàm/số dòng cũ là điểm dò, không thay mã mới.

Nền nghiên cứu là Javis 0.284.0, `a02e45fcd7c58bb3fde4eac3a850cb87c618621c`. Lúc thực hiện phải lấy main mới nhất, ghi snapshot mới và làm gap analysis trước khi code. Không triển khai lại phần main đã có và đạt.

Nếu source trên main khác spec, giữ mục tiêu và bất biến; ghi quyết định điều chỉnh interface theo mã thật. Không lặng lẽ hạ phạm vi, bỏ gate hoặc sửa tiêu chí đạt để công việc dễ kết thúc. Xung đột có ảnh hưởng ý định/chất lượng/quyền phải báo chủ, trong khi tiếp tục phần độc lập đã được phép.

## 3. Quyền thực hiện khi chủ phát Goal này

Tôi duyệt hướng thiết kế và phạm vi M0–M8 của spec để bắt đầu triển khai. Tôi ủy quyền cho phiên thực hiện:

- Khảo sát mã, tự viết và tự rà soát implementation plan của từng mốc, rồi thực hiện mà không dừng hỏi tôi duyệt lại từng plan, từng bước hoặc “có tiếp tục không”. Đây là lựa chọn thực hiện liên tục của tôi cho công việc này.
- Sửa mã, tài liệu, test, migration và UI liên quan; chạy build/test/checker, dựng môi trường thử và sửa lỗi tìm thấy trong phạm vi Goal.
- Tạo hoặc tái sử dụng worktree phù hợp; tạo nhánh `codex/agent-skill-improvement-*`; commit, push, mở PR và merge theo quy ước repo khi CI và review bắt buộc đạt. Không bỏ gate CI, không force-push nhánh chia sẻ và không merge thay đổi chưa kiểm.
- Triển khai trực tiếp theo từng mốc. Có thể dùng subagent để review hoặc xử lý phần độc lập nếu công cụ/môi trường cho phép; chủ thể chính giữ trách nhiệm tích hợp và kiểm chứng. Không tạo chat sidebar mới hay nhắn task khác không liên quan.
- Chạy model-backed smoke/feasibility trong môi trường thử biệt lập bằng engine/model/reasoning tôi đã chọn và tài khoản đã cấu hình cho tác vụ phát triển, khi cấu hình đó được xác minh. Các lượt này dùng quota/tài nguyên thật; ghi đầy đủ usage. Không tự mua dịch vụ, thêm tài khoản hoặc đổi cấu hình model để chạy được.
- Dùng fixture có nhãn mô phỏng để kiểm kỹ thuật; đọc nguồn nghiệp vụ đã có quyền đọc trong phạm vi dự án/brain được giao để chuẩn bị snapshot. Không tự mở rộng sang brain/tài khoản khác và không lấy dữ liệu riêng không thuộc tác vụ.
- Áp/revert candidate, kiểm canary với clock giả và nghiệm thu UI tự động trên brain thử dùng một lần; luôn ghi rõ đó là kiểm kỹ thuật, không live canary.

Lệnh này không tự thay thế quyết định nghiệp vụ cần tôi chấm, không cấp quyền tự bật `auto_scoped`/activation trên brain thật, không duyệt gửi/đăng/thanh toán, không cho sửa luật chủ, không cấp quyền lấy secret vào workspace. Deploy/restart server thật, rollout live và đọc nguồn chưa được cấp cần quyền cụ thể từ phiên hoặc môi trường; không suy từ quyền merge PR.

Không dừng để xin lại những quyền đã có trong Goal này. Chỉ hỏi khi thật sự thiếu thông tin/quyền/nguồn mà không thể lấy từ cấu hình và bằng chứng được phép đọc. Gom câu hỏi còn thiếu thành yêu cầu cụ thể, tránh hỏi rải rác từng việc nhỏ.

## 4. Khởi tạo và bảo toàn công việc hiện có

1. Kiểm tra nhánh, git status, worktrees, PR và VERSION. Checkout chính `D:\Project\Javis-OS` có thể cũ và có nhiều thay đổi của người khác; không reset, clean, đổi nhánh hoặc ghi đè chúng.
2. Đọc các file nguồn chuẩn ngay tại vị trí hiện có. Spec có thể là file chưa commit; sao chép đúng những tài liệu cần dùng vào worktree triển khai, ghi hash nguồn. Không mang cả thay đổi chưa commit không liên quan vào nhánh mới.
3. Ưu tiên worktree đang phù hợp thuộc cùng công việc; nếu chưa có thì tạo từ main mới nhất. Không đụng worktree của chat khác.
4. Kiểm các chức năng/phụ thuộc đã phát hành. Tạo bảng `existing -> missing -> evidence -> milestone`; phần đã đạt có bằng chứng thì ghi reused, không viết lại.
5. Chạy baseline test liên quan trên nền sạch và ghi kết quả. Test đỏ có sẵn không được coi là đạt; xác định nguyên nhân/mức ảnh hưởng và xử lý blocker trong phạm vi. Không chỉnh assert hoặc nới quyền để biến đỏ thành xanh.
6. Trước khi code mỗi PR phát hành, đọc và tuân thủ quy trình xí chỗ version/CHANGELOG của repo hiện hành. Đồng bộ main, tránh trùng version và giải quyết conflict cần thiết.
7. Bắt đầu hồ sơ tiến độ ở `docs/superpowers/status/agent-skill-improvement-progress.md` trong worktree triển khai. Không đánh dấu mốc done chỉ vì có file plan.

## 5. Cách làm liên tục qua các mốc

Thứ tự mặc định: **M0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> M8**. Phụ thuộc thật theo mục 23 của spec quyết định khả năng thực thi. M4/M5 chia sẻ effective view; M7 phải có nền nghiệm thu và usage Team tương ứng. Không mở rollout khi gate phụ thuộc chưa đạt.

Mỗi mốc thực hiện trọn vòng:

1. Đối chiếu mục tiêu, test IDs và interface với main mới, viết implementation plan đủ rõ để thực hiện.
2. Tự review plan theo spec, sửa thiếu sót rồi code. Không ngừng ở kế hoạch chờ một câu “tiếp”.
3. Viết test hành vi có ý nghĩa, thấy lỗi đúng khi thiếu triển khai, rồi làm mã chạy đúng. Giữ test nhanh và kiểm hồi quy tương xứng.
4. Chạy kiểm liên quan, migration/recovery/quyền/scope và UI cần thiết. Nếu lỗi, tìm nguyên nhân rồi sửa; không lặp nguyên cách thất bại hoặc hạ tiêu chí.
5. Review diff và bằng chứng; dùng reviewer độc lập khi có công cụ, ghi phạm vi review thật. Reviewer không làm thay nghiệm thu của chủ và không có quyền tự chấp nhận sản phẩm sai.
6. Cập nhật trạng thái kỹ thuật/pilot/rollout, log test thực đã chạy, hạn chế và bước kế tiếp.
7. Mở/cập nhật PR đủ nhỏ, hoàn tất CI/review theo repo, merge khi đủ điều kiện và chuyển mốc tiếp từ nền đã tích hợp. Nếu một PR chờ ngoại cảnh, xử lý phần độc lập an toàn thay vì mở hàng loạt nhánh chồng chưa kiểm.

Được tiếp tục xây phần kỹ thuật của mốc sau trên nền đã kiểm khi chỉ đang chờ chủ chấm hoặc thời gian live canary, nhưng phải để nhãn **engineering-only/not released**, không vượt gate sử dụng/rollout, không giả mốc phụ thuộc đã nghiệm thu toàn bộ. Một mốc có prerequisite kỹ thuật chưa đạt thì sửa nền trước.

Không rút gọn M4 thành chỉ có một interface adapter rỗng hoặc M8 thành nút bật tắt. Mỗi adapter/surface công bố hỗ trợ phải có enforcement và kiểm thực tương ứng; môi trường không thể kiểm engine nào thì ghi chưa hỗ trợ/chưa xác minh cho engine đó và giữ mốc mở theo phạm vi đã chốt.

## 6. Các bất biến phải giữ trong toàn bộ Goal

- Chất lượng và độ đúng trước chi phí. Không tự đổi model/provider/reasoning, thêm trần USD mặc định, bỏ dữ liệu cần thiết hoặc bỏ kiểm chứng để giảm usage.
- Giữ đường model cấu hình hiện có. Không chạy engine thật nếu chưa xác định được engine được chủ chọn; không dùng engine khác thay thế âm thầm.
- Baseline luôn có trong so sánh; candidate thua hoặc chưa rõ thì giữ baseline. Không viết gate bắt buộc candidate phải thắng.
- Hợp đồng/checker/đáp án được pin và nằm ngoài vùng sửa của solver/improver; qualification kín, tiêu thụ đúng vòng, không chỉnh candidate bằng đáp án rồi dùng lại bộ ấy như holdout mới.
- Trial dùng workspace và context sạch, không sửa canonical/live memory, không side effect. Native shell/web/connector phải được chặn thực bằng host; prompt không phải sandbox.
- Revision/effective view theo scope, receipt và cache generation phải đi xuyên mọi đường nạp đã enable; không nạp nhầm mirror hoặc ảnh hưởng scope khác.
- Giữ nguyên luật chủ, model/reasoning, quyền, disabled state và ownership. Protected content có cả ý nghĩa; giữ chữ gốc rồi thêm câu trái luật vẫn không đạt.
- Reuse ledger, usage, lessons, scheduler và nghiệm thu hiện có. Không tạo runtime/sổ tiền/goal giả để né schema.
- CAS/lease/idempotency, crash recovery, late result, cancellation và rollback phải kiểm thật. Không quay lại bằng cách ghi đè sửa mới của chủ hoặc phục hồi quyền đã thu hồi.
- Trial/compose/qualification không trở thành source tự học/đặt việc production. Không gom nhiều agent đồng tình thành bằng chứng độc lập.
- Unknown, interrupted, quota, thiếu telemetry hoặc thiếu nạp phiên bản phải được ghi đúng. Không đổi thành pass hoặc 0 usage.

## 7. Pilot, nghiệm thu và khoảng chờ thực tế

Làm đủ feasibility, pilot A/B và bộ kiểm của các mốc tương ứng theo spec. Chọn nguồn/context từ những gì chủ đã cấp; nếu chưa có, chuẩn bị đầy đủ runner/report và nêu chính xác nguồn, engine hoặc người chấm cần bổ sung. Fixture chỉ chứng minh cơ chế, không được dùng để tuyên bố hiệu quả nghiệp vụ thực.

Không sửa ngưỡng thống kê hoặc cherry-pick case để đạt. Candidate bị rejected/inconclusive là một kết quả hợp lệ; hệ phải xử lý đúng và giữ baseline. Vẫn xem xét giả thuyết có bằng chứng mới trong phạm vi spec, không chạy vô hạn tới khi thắng. Không tự mở rộng rollout chỉ vì mã đã xong.

Live canary đòi **thời gian thật và nhiệm vụ thật** theo spec. Clock giả, tua thời gian hoặc synthetic tasks chỉ được ghi là test kỹ thuật. Chưa đủ 7 ngày/10 việc hoặc thiếu owner confirmation thì gate còn mở; không gọi Goal hoàn thành toàn bộ.

Khi phải chờ quota/thời gian/người chấm, giữ checkpoint và tiếp tục phần độc lập. Không gọi model lặp để báo “vẫn đang chờ”, không tự tạo recurring automation ngoài quyền được chủ cấp. Nếu phiên/môi trường không hỗ trợ tiếp tục qua khoảng chờ, báo rõ checkpoint và điều kiện resume; không hứa sẽ tự thức dậy khi chưa có cơ chế.

Một phép thử kỹ thuật được phép chạy trong brain tạm không đồng nghĩa đã cho phép canary trên brain thật. Đề nghị live rollout phải là một kết quả cụ thể để chủ xem: revision, scope, evidence, model config, nguồn và đường rollback; tận dụng quyền đã cấp nếu phiên đã có quyết định tương ứng.

## 8. Hồ sơ tiến độ và cách tiếp tục sau gián đoạn

File tiến độ giữ tối thiểu:

- Goal/spec/source hashes, snapshot main và phiên bản hiện tại.
- Worktree/branch/PR URL/commit/CI và quyết định review từng mốc.
- Bảng M0–M8 với ba cột **kỹ thuật**, **bằng chứng nghiệp vụ**, **điều kiện rollout**, kèm lý do nếu chưa đạt.
- Test commands đã chạy, kết quả thật và evidence refs; phân biệt fake/real smoke/pilot/live canary.
- Candidate/base/config/case-set ids, trạng thái qualification, activation và pending owner actions.
- Blocker có nguồn, việc đã thử, phần độc lập tiếp tục được và bước tiếp theo cụ thể.

Cập nhật sau mỗi đơn vị có ý nghĩa hoặc trước khi kết thúc lượt; không ghi secret/PII vào progress. Phiên mới phải đọc progress và đối chiếu state/PR thật, không làm lại mốc đã xong hoặc tin một checkbox thiếu chứng cứ.

Không dừng ở cuối một mốc chỉ để tóm tắt. Báo tiến độ ngắn rồi tiếp tục tới phần chưa đạt. Nếu môi trường có lifecycle Goal, dùng đúng trạng thái/lưu lượng của môi trường; không tự đánh dấu complete vì gần hết context hoặc vì đã viết báo cáo.

## 9. Khi nào thật sự cần chủ can thiệp

Chỉ nêu blocker khi không thể tự giải bằng quyền và nguồn sẵn có, ví dụ: thiếu credential/cấu hình engine được chọn, thiếu nguồn nghiệp vụ độc lập, tiêu chí cần chủ chấm, quyền live chưa cấp, quota chưa mở hoặc khoảng canary chưa đủ. Trước đó phải làm các phần chuẩn bị/kiểm kỹ thuật còn làm được.

Báo gọn: mốc/phần nào bị chặn, bằng chứng, đã thử gì, cần đúng thông tin/quyết định nào và các phần khác đang tiếp tục. Không báo toàn Goal xong, không tự thay bằng một phạm vi nhỏ hơn và không yêu cầu anh phê duyệt lại toàn bộ.

Nếu tự review phát hiện spec mâu thuẫn hoặc không thể hiện thực nguyên văn, đưa phương án sửa nhỏ nhất giữ ý định và bằng chứng. Quyết định kỹ thuật thường lệ tự giải; thay chính sách quyền, định nghĩa thành công, dữ liệu sử dụng hoặc trade-off chất lượng trọng yếu cần chủ quyết.

## 10. Điều kiện hoàn thành Goal

Chỉ đánh dấu **hoàn thành toàn bộ** khi:

1. M0–M8 đều có sản phẩm thực thi và bằng chứng tương ứng theo spec, không có scaffold/placeholder/adapter giả được công bố như tính năng đã có.
2. Các test bắt buộc, kiểm hồi quy, migration/recovery/quyền/scope/rollback và review yêu cầu đạt trên snapshot cuối; CI xanh cho mọi thay đổi tích hợp.
3. Pilot có kết luận trung thực cho từng nghiệp vụ/config, gate nội dung cần chủ chấm đã được xử lý; không dùng test giả thay bằng chứng thực.
4. Các phạm vi live được cho phép đã qua đủ điều kiện canary, hoặc chủ đã quyết rõ phạm vi đó không phát hành sau kết quả rejected/inconclusive. Những phần chưa có quyền/nguồn/quan sát không tự được coi là đã đạt.
5. Mọi code thay đổi của Goal đã được commit/PR/tích hợp theo quy ước; không lẫn thay đổi người khác, không bỏ sót source cần chạy.
6. Có tài liệu sử dụng/vận hành/khôi phục, progress đầy đủ và báo cáo cuối gồm bản phát hành, PR/commit, bằng chứng thật, phạm vi hỗ trợ và hạn chế.

Chủ quyết không phát hành một scope do không tạo giá trị là quyết định sản phẩm hợp lệ, không là bằng chứng candidate tốt. Mọi thay đổi giảm phạm vi Goal cần chủ xác nhận rõ; không dùng một report “chưa đủ bằng chứng” để tự hoàn thành các mốc còn thiếu.

Báo cáo cuối phải tách **đã triển khai**, **đã kiểm bằng model thật**, **đã chứng minh giá trị trong phạm vi nào**, **đã phát hành/bật ở đâu** và **còn phần nào bị chặn**. Nếu còn blocker bắt buộc, Goal vẫn chưa hoàn thành toàn bộ và lifecycle phải phản ánh điều đó theo khả năng môi trường.

## 11. Tham chiếu cú pháp Goal

[OpenAI Docs: Using Goals in Codex](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex) mô tả `/goal <outcome>` và mục tiêu có điều kiện kiểm chứng qua nhiều lượt. Tài liệu này dùng cấu trúc đó; không bảo đảm một Goal có thể vượt quyền, quota, gián đoạn môi trường hoặc rút ngắn thời gian canary thật.

