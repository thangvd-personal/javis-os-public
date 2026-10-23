# Spec cải thiện Team: nghiệm thu mặc định, chất lượng trước chi phí, kiểm chứng bằng nghiệp vụ thật

- Ngày: 22/10/2026, Asia/Saigon.
- Trạng thái: bản đề xuất hoàn chỉnh để chủ sản phẩm xem xét; chưa triển khai mã, chưa chạy pilot.
- Nền đã khảo sát: Javis OS **0.281.0**, `d678140467211000681805d3e2f82968a499b111` trên `origin/main`.
- Bản nguồn đối chiếu trên máy: `exports/team-swot-20261022-d6781404/`. Nhánh làm việc hiện tại cũ hơn nền này; khi triển khai phải bắt đầu từ main mới và đối chiếu lại các điểm tích hợp.
- Chủ sản phẩm và người nghiệm thu nghiệp vụ: anh. Tài liệu này đề xuất hành vi sản phẩm, dữ liệu, kiểm thử và điều kiện phát hành; không phải lệnh tự chạy model, đổi model, triển khai hay gửi ra kênh ngoài.

## 1. Mục tiêu và các quyết định đã chốt

Team phải tạo ra kết quả dùng được, giảm thời gian chủ phải điều phối và kiểm tra, đồng thời giải thích được nguồn lực đã sử dụng.

Thứ tự ưu tiên: **nghiệm thu mặc định -> minh bạch chi phí và giảm lãng phí -> thử nghiệp vụ thực tế -> mở rộng có chọn lọc**.

Yêu cầu trực tiếp của chủ: **không để việc eo hẹp chi phí ảnh hưởng đến kết quả**. Vì vậy:

1. Không bổ sung trần tiền mặc định để dừng việc đang làm.
2. Không tự đổi sang model rẻ hơn, hạ mức suy luận, rút bớt đầu vào cần thiết hoặc bỏ kiểm chứng để tiết kiệm.
3. Chi phí cao, vượt mức tham khảo hoặc không xác định được giá không phải lý do chặn việc, xin duyệt chi phí hay kết luận thất bại.
4. Tối ưu chỉ được bật khi có bằng chứng không làm giảm chất lượng và không bỏ qua công việc cần thực hiện.
5. Giữ cổng quyền, giới hạn tài nguyên máy, điều kiện dừng của chủ, chống lặp và hạn mức thực của provider. Các cơ chế này phải có lý do riêng, không được trình bày thành một trần tiết kiệm tiền.

Các con số về thời gian và tỷ lệ trong spec là **mục tiêu thử nghiệm**, chưa phải kết quả Javis đã đạt. Hai nghiệp vụ pilot được chọn làm phạm vi khởi đầu, không giả định dữ liệu bán hàng của chủ đã kết nối.

## 2. Hiện trạng đã xác minh và khoảng trống

| Thành phần trên nền 0.281.0 | Đã có | Khoảng trống cần xử lý |
|---|---|---|
| `team_runner.py`, `team_acceptance.py` | Kiểm sản phẩm, SHA-256, vòng sửa, xác nhận của chủ | Tiêu chí là tùy chọn; `_finish` vẫn đóng việc không có hợp đồng nghiệm thu |
| Nghiệm thu | `file_exists`, `file_contains`, `json_keys`, `command_ok`, `owner_confirm` | Chưa có hợp đồng mặc định cho mọi cửa tạo việc; xác nhận kết quả chữ cần gắn đúng nội dung; cấu trúc đúng chưa đủ chứng minh nghiệp vụ đúng |
| `team_store.py`, `agent_ledger.py` | Lượt, trạng thái, token/chi phí nếu có, đối soát | Lượt cấp Team khác số request model bên trong; dữ liệu phản chiếu dễ bị cộng trùng nếu ghép sai |
| `usage_store.py`, `usage_index.py` | Usage nhiều provider, log API, index log CLI | Sự kiện chưa có đầy đủ quan hệ team/job/step/turn; chi phí gói thuê bao, ước tính và khoản tính theo API chưa tách rõ ở mọi chỗ |
| Giới hạn Team | Mặc định 12 lượt/việc, 100 lượt/ngày/nhóm, cùng các giới hạn khác | Cần phân biệt hạn mức tiết kiệm mặc định với giới hạn vận hành, quyền và hạn mức chủ tự cấu hình |
| Mục tiêu phòng ban | Quan sát file, việc lỗi, test và tình trạng kết nối MCP | MCP đang sống không chứng minh doanh thu/tồn kho đã được lấy hoặc đối chiếu |
| Pilot | Đã có bộ xuất trace và kịch bản nhiều ngày | Gói nhiều ngày còn ghi chưa chạy; chưa có so sánh đủ điều kiện giữa Team và một trợ lý |

Lần kiểm tra local trước spec: 59/62 tệp liên quan Team đạt. Ba tệp còn vấn đề về đường dẫn/quyền symlink trên Windows, ảnh hưởng MCP riêng của máy vào test factory, và cửa sổ thời gian engine giả quá ngắn. Chẩn đoán hai tệp sau đã chạy đạt khi cô lập nguyên nhân; không coi bộ gốc đã xanh. P0 phải xử lý bằng kiểm thử có thể lặp lại, không nới quyền ứng dụng để làm test đạt.

## 3. Phạm vi và lựa chọn thiết kế

Chọn **mở rộng cơ chế hiện có, phát hành theo từng mốc**. Không tạo bộ điều phối hoặc sổ chi phí độc lập thứ hai.

Hai phương án không chọn cho đợt này:

- Chỉ thêm một bảng nhắc chủ tự đặt nghiệm thu: nhanh nhưng vẫn có cửa bỏ sót và báo Xong sai.
- Viết lại toàn bộ Team, cơ chế nhiều agent hoặc router tự chọn model theo giá: phạm vi quá lớn, tăng rủi ro, không trực tiếp chứng minh lợi ích nghiệp vụ.

Trong phạm vi: hợp đồng mặc định và mẫu; xác nhận gắn phiên bản sản phẩm; trạng thái đúng; số đo chi phí có nguồn; tối ưu chống trùng có điều kiện; hai nghiệp vụ pilot có đối chứng; migration, phục hồi và kiểm thử.

Ngoài phạm vi: tự thay model theo giá; hard cap tiền mới; thanh toán/hạch toán; tự đăng bài, nhắn khách hoặc đặt hàng trong pilot; huấn luyện lại model; cơ chế KPI kinh doanh tự chủ toàn diện; viết lại đồ thị phụ thuộc hay cách ly worktree cho mọi agent. Các giới hạn chia việc và ghi chung file vẫn phải được ghi trong báo cáo pilot, không coi đã được giải quyết bởi spec này.

## 4. P0: hợp đồng nghiệm thu mặc định

### 4.1 Áp dụng từ lúc tạo việc

Mọi việc Team mới, dù tạo từ chat/Giao việc, công cụ `javis_team`, trưởng nhóm, mandate hay đường API nội bộ, đều phải có một hợp đồng nghiệm thu trước bước thực thi đầu tiên. Chat thuần không tạo sản phẩm vẫn giữ nguyên, không phát sinh hợp đồng hay việc nền.

Thứ tự lấy hợp đồng:

1. Tiêu chí chủ truyền rõ cho việc.
2. Mẫu nghiệp vụ do chủ chọn hoặc đã cho phép áp cho nguồn việc đó.
3. Mẫu mặc định của nhóm do chủ cấu hình.
4. Nếu chưa có mẫu phù hợp: host tự gắn `owner_confirm` cho bản kết quả cuối. Việc vẫn được làm ngay, kết thúc ở Chờ anh nghiệm thu.

Không gọi thêm một model chỉ để dựng hợp đồng cho trường hợp đã có mẫu. Agent có thể đề xuất tiêu chí bổ sung nhưng không được tự sửa, xóa hay hạ tiêu chí đã có. Mẫu chỉ được áp khi thỏa bộ điều kiện đã cấu hình, không đoán loại việc từ vài từ rồi tự cấp quyền tự nghiệm thu.

Mỗi hợp đồng có:

| Trường | Ý nghĩa |
|---|---|
| `task_id`, `team_id`, `job_revision` | Việc và bản yêu cầu mà hợp đồng áp dụng |
| `contract_version`, `contract_hash` | Phiên bản tiêu chí bất biến trong một lần kiểm |
| `origin` | `owner`, `template`, `team_default`, `fallback` |
| `template_id`, `template_version` | Nguồn mẫu; rỗng khi không dùng mẫu |
| `criteria`, `expected_outputs` | Điều kiện đạt, nơi và loại sản phẩm mong đợi |
| `source_refs` | Nguồn đã chốt cùng version/hash hoặc dấu thời gian và trạng thái không xác định |
| `fix_rounds` | Tối đa hai vòng tự sửa theo cùng hợp đồng ở mặc định, không phải trần tiền |
| `set_by`, `created_at` | Ai đặt và lúc đặt; host không nhận danh tính từ lời model |

Mỗi việc chụp bản mẫu của riêng nó. Sửa mẫu chỉ áp cho việc tạo sau đó. Sửa hợp đồng của việc đang chạy phải qua hành động của chủ, tăng version và làm kết quả kiểm cũ hết hiệu lực.

### 4.2 Không để việc chạy lọt khi hợp đồng đang được ghi

Hàng đợi việc ở `kanban.sqlite3`, hợp đồng ở `team.sqlite3`; không giả định một khóa Python là giao dịch nguyên tử giữa hai kho.

- Tạo việc với dấu `acceptance_ready=false`, chưa đủ điều kiện claim.
- Ghi hợp đồng idempotent theo task/revision, rồi đánh dấu ready khi quan hệ đã được xác minh.
- Dispatcher kiểm ready và hợp đồng tồn tại, khớp revision trước khi claim; mọi cửa tạo việc dùng cùng một thao tác host.
- Crash trước khi ghi xong: khởi động lại gắn fallback hoặc hoàn tất hợp đồng đã lưu, không chạy việc chưa có hợp đồng.
- Thao tác tạo lại với cùng `client_msg_id` trả lại cùng việc và cùng hợp đồng; không tạo bản sao.
- `_finish` có chốt cuối: thiếu hợp đồng hợp lệ thì chuyển sang chờ nghiệm thu/cần chuẩn bị, tuyệt đối không gọi nhánh đóng thẳng cũ.

### 4.3 Nghiệm thu cả file và kết quả chữ

Giữ các loại tiêu chí hiện có. Mở rộng `owner_confirm` bằng `target=file|message`:

- `file`: giữ tương thích trường `path`; host băm file thực tế trong thư mục được phép.
- `message`: host gắn mã tin kết quả cuối và SHA-256 của nội dung đã lưu, không lấy chuỗi model tự khai là hash. Fallback dùng loại này để công việc trả lời bằng chữ không bị ép tạo file rỗng.
- Nhiều sản phẩm: `expected_outputs` là manifest; chủ xác nhận manifest gồm mọi sản phẩm bắt buộc và hash tương ứng.
- Không chấp nhận file từ trước việc làm bằng chứng cho sản phẩm mới chỉ vì file có tồn tại. Muốn dùng lại sản phẩm cũ phải được hợp đồng nêu rõ là nguồn tái sử dụng, có version/hash.

Kiểm bằng máy phải phân biệt: tồn tại, đúng cấu trúc, đúng số liệu và đúng nghiệp vụ. `json_keys` không được hiển thị thành “đã kiểm đúng nội dung”; `file_contains` không được hiển thị thành “đã kiểm chính xác giá”.

Các mẫu có kiểm số liệu dùng `command_ok` với checker xác định, nguồn dữ liệu và phiên bản checker được chốt. Agent làm sản phẩm không được sửa checker/fixture để làm tiêu chí đạt. File checker ngoài phạm vi ghi của bước, được bảo vệ như cấu hình hệ thống; hash thay đổi khiến kiểm tra thành `unknown` và chờ chủ, không dùng checker mới im lặng.

### 4.4 Luồng trạng thái và sửa lại

| Tình huống | Trạng thái hiển thị | Xử lý |
|---|---|---|
| Còn bước chưa chạy, đang chạy hoặc chờ duyệt hành động | Đang làm / Đang chờ | Chưa bắt đầu đóng việc |
| Có sản phẩm, đủ đầu vào kiểm | Đang kiểm sản phẩm | Chạy checker, lưu evidence |
| Mọi tiêu chí khách quan đạt, hợp đồng cho tự nghiệm thu, không có xác nhận của chủ | Đã nghiệm thu | Đóng việc và ghi nhận hữu ích đúng một lần |
| Có tiêu chí fail | Cần sửa | Giao lại phần lỗi cùng evidence; giữ phần đạt nếu còn hiệu lực |
| Chưa đủ căn cứ hoặc cần `owner_confirm` | Chờ anh nghiệm thu | Không done, không tự gán pass bằng nhận xét model |
| Hai vòng tự sửa vẫn không đạt | Cần đổi cách xử lý | Giữ kết quả, nêu lỗi và phương án; chủ có thể giao tiếp, sửa yêu cầu hoặc hủy |
| Sản phẩm đổi sau nghiệm thu | Bản hiện tại cần kiểm lại | Giữ chứng cứ bản cũ; không hiển thị bản mới là đã đạt |

Hai vòng là giới hạn thử tự động cùng một cách, nhằm tránh vòng lặp vô ích. Không buộc chủ chấp nhận kết quả kém để kết thúc và không gắn lý do “hết ngân sách”. Có tiến triển nhưng cần hướng mới được trình bày thành đề nghị xử lý tiếp, không tự hạ tiêu chí.

Một lần kiểm phải gắn bộ `(job_revision, contract_version, round, output_manifest_hash, source_snapshot_hash)`. Kết quả model/checker đến muộn, thay nguồn hoặc thay tiêu chí đều không được áp vào bản mới.

Chủ bấm nhận kết quả phải truyền bộ phiên bản đang xem cùng `decision_key`. Gửi lại cùng khóa trả kết quả cũ; tab khác quyết bản cũ nhận `stale_card`. Kiểm hash thực trước khi chấp nhận, không chỉ so kích thước/mtime. Một tiêu chí máy đang fail không được đổi thành pass chỉ vì bấm xác nhận chung: chủ có thể sửa hợp đồng có lịch sử, hoặc ghi ngoại lệ rõ ràng được hiển thị riêng, không gắn nhãn “mọi tiêu chí đã đạt”.

Khi phát hiện sản phẩm đã thay đổi sau lần đạt, giữ `done` như lịch sử của lần bàn giao cũ nhưng trả `completion_valid=false` cho sản phẩm hiện tại. Dashboard và thống kê nghiệm thu phải dùng giá trị này. Không tự mở lại bước thực thi, không tự gửi lại hành động ngoài; nút Kiểm lại chỉ khởi động kiểm tra mới và sau đó mới quyết định có cần sửa.

### 4.5 Quyền và giới hạn bằng chứng

- Chỉ chủ hoặc mẫu chủ đã chọn quyết định tiêu chuẩn; lời agent và biểu quyết của nhiều agent là evidence, không là quyền thay tiêu chuẩn.
- Duyệt hành động trước khi làm và nghiệm thu sản phẩm sau khi làm là hai quyết định khác nhau. Duyệt một hành động không làm việc tự đạt nghiệm thu; nghiệm thu không cho phép gửi/đăng/thanh toán.
- Đường dẫn, symlink/junction và giới hạn đọc phải được kiểm thống nhất Windows/Linux, kể cả đường có gốc nhưng chưa có ký tự ổ đĩa. Không được nới sandbox để giải quyết lỗi test.
- Không hỗ trợ kiểm khách quan cho một loại sản phẩm thì trả unknown kèm lý do; không giả pass hay tự cài thư viện trong đường chạy.
- Không chạy lệnh kiểm sản phẩm có tác động ra ngoài; giữ cơ chế quyền và allowlist hiện tại.

## 5. P1: minh bạch chi phí, chất lượng trước

### 5.1 Thông tin chủ cần thấy

Mỗi việc có thẻ Mức sử dụng: tổng mức dùng, phần đã biết/chưa biết, theo lập kế hoạch, làm, hỗ trợ, tổng hợp, kiểm chứng/sửa; số lượt Team; số request model thực sự quan sát được; thời gian chạy và chờ. Không đồng nhất một lượt Work với một request API.

| Loại khoản | Nhãn hiển thị | Quy tắc |
|---|---|---|
| Chi phí provider trả về cho API tính theo dùng | Provider báo | Có nguồn và đơn vị; chưa mặc định gọi là hóa đơn đã quyết toán |
| Tính theo bảng giá và usage | Ước tính | Ghi phiên bản bảng giá, cách tính và phần thiếu; không cộng cùng khoản provider báo |
| Gói thuê bao | Dùng trong gói | Hiện token/lượt/hạn mức nếu có; không ghi “miễn phí”, không áp đơn giá API thành tiền thực phải trả |
| CLI trả số quy đổi API khi đang dùng thuê bao | Giá trị quy đổi tham khảo | Tách khỏi tiền API và phí thuê bao, không cộng vào tổng tiền thực |
| Model/giá/token chưa đủ dữ liệu | Chưa xác định | Giữ null và mức độ bao phủ; không thay bằng 0 |

Nếu biết một phần, hiển thị “phần đo được” và số lượt thiếu dữ liệu, không gắn nhãn tổng đầy đủ. Không quy đổi ngoại tệ ngầm; bản đầu hiển thị USD cho khoản USD, phí thuê bao gốc riêng nếu chủ cung cấp. Không tự suy ra lợi nhuận hoặc doanh thu từ token.

### 5.2 Nguồn số liệu và chống cộng trùng

Mở rộng đường `usage_store.record`, parser/index và ngữ cảnh lượt; không tạo kho tính tiền độc lập với trang Mức dùng.

- Mọi lần thực thi có định danh host: `team_id`, `task_id` nếu gắn việc, `step_id`, `turn_id`, `attempt`, `provider`, `model`, `purpose`, `job_revision`, thời gian. Quan hệ được truyền cả API in-process và CLI, không lấy từ chữ model.
- Một lượt engine có thể có nhiều request; khóa usage là định danh request của provider hoặc định danh sự kiện ổn định trong log, kèm phạm vi provider/session. Retry thực sự tiêu tài nguyên phải có attempt riêng, không bị chống trùng nhầm.
- Với API: sự kiện usage của engine là nguồn chuẩn. Với CLI: log chuẩn được parser khôi phục ở mức request khi có thể. Số tổng cuối lượt chỉ là nguồn dự phòng khi chưa có log chi tiết.
- Khi log chi tiết tới sau, thay thế bản tổng dự phòng trong projection, không cộng thêm. Nếu không thể liên kết chắc chắn, giữ hai nguồn ở trạng thái chưa đối soát và không cộng thành tổng chắc chắn.
- `team_turns` và `agent_calls` là sổ thực thi và quan hệ phản chiếu; không cộng cả hai thành hai lần chi phí. Tổng số lượt từ agent ledger và số request từ usage có thể khác nhau, nhãn phải nói rõ.
- Giữ cả khoản của lần lỗi/hủy đã thực sự gọi model. Trước mốc gọi được hoàn về không gọi; sau mốc mà thiếu usage ghi interrupted/unknown, không tự hoàn tiền hay giả usage bằng 0.
- Lượt phán xử, tự học, quét và chủ động có `purpose` riêng. Không thuộc một việc cụ thể thì tính phần chung của nhóm; không chia đều tùy ý cho mọi việc rồi cộng lần nữa ở cấp nhóm.
- Index là projection có thể dựng lại. Bổ sung sự kiện và định danh bền vào nguồn hiện có; replay sau restart phải cho cùng tổng và cùng mức độ bao phủ.
- Lỗi ghi/thống kê chi phí không làm hỏng việc: hiện cảnh báo thiếu dữ liệu, tiếp tục thực thi, khôi phục từ nguồn còn lại khi có thể; không tuyên bố đã đo đủ nếu nguồn đã mất.

Không ghi thêm prompt, dữ liệu khách hàng hoặc token đăng nhập vào sổ usage. Ghi định danh, số đo và tham chiếu bằng chứng cần thiết.

### 5.3 Mức tham khảo và các giới hạn đang tồn tại

Chế độ mới `quality_first` có `cost_behavior=observe_only`. Mức chi phí tham khảo là tùy chọn, mặc định không đặt. Vượt mức chỉ cập nhật thẻ việc; nếu chủ bật thông báo thì tối đa một thông báo cho một mức của một việc, không mở hộp duyệt và không thay luồng chạy.

Phải xử lý cả các trần lượt cũ, không chỉ bỏ một trần USD:

- Với việc được chủ giao trong nhóm bật quality_first, các preset lượt/việc và lượt/ngày nhằm tiết kiệm của ứng dụng chuyển thành mức tham khảo, không cắt ngang việc hay bỏ kiểm chứng. Không để một gate cũ phía agent budget âm thầm tái áp cùng trần cho nghĩa vụ của việc.
- Giữ giới hạn đồng thời, lease, timeout, độ sâu nhờ hỗ trợ và nhận diện không tiến triển vì đây là cơ chế vận hành. Không tự tăng quyền hay số agent khi vượt mức tham khảo.
- Với hoạt động tự phát như quét, tự lên tiếng, tự sinh việc: giữ quy tắc có sự kiện hữu ích và nguồn được cấp; không biến quality_first thành nền tự gọi model không giới hạn. Hạn mức tự phát không được lấy mất quyền hoàn tất việc chủ đã giao.
- Trần quản trị máy chủ như `JAVIS_RESONANCE_CALL_CEILING`, quota/rate limit provider, lệnh dừng và trần chủ đã đặt rõ ràng vẫn được tôn trọng. Nếu chúng chặn việc, ghi đúng loại và lý do, giữ sản phẩm, không báo Xong và không tự đổi sang model rẻ hơn.
- Nhóm cũ có số trần nhưng không biết đó là preset hay chủ đã tự đặt: migration không được tự đoán và xóa. Khi chuyển nhóm đó sang quality_first, giao diện cho chủ chọn một lần cách giữ/chuyển các trần hiện có; không hỏi lại cho từng lượt. Nhóm mới dùng mặc định quality_first sau rollout.

Không làm thêm tùy chọn hard cap tiền trong phạm vi spec. Nếu sau này chủ yêu cầu một giới hạn tiền cụ thể thì đặc tả riêng, không mặc định bật dựa trên tài liệu này.

### 5.4 Giảm lãng phí có điều kiện

Ưu tiên chống gửi trùng, chạy lại phần lỗi thay vì cả việc, reuse dữ liệu nguồn không đổi trong cùng phạm vi quyền, và tránh phán xử khi không có tín hiệu mới. Không cache hoặc replay một hành động ngoài đã làm thành một hành động mới.

Cache đọc nếu triển khai phải có khóa nguồn+version/hash+brain+quyền+tham số; dữ liệu thời gian thực dùng freshness theo mẫu đã chọn. Nguồn đổi, quyền thu hồi hoặc yêu cầu mới cần đọc lại thì bỏ cache. Không chứng minh được độ tươi thì đọc bình thường. Lỗi cache hay telemetry không được làm việc thất bại.

Đề nghị tối ưu chỉ là gợi ý nếu ảnh hưởng cách chọn model, mức suy luận, số vai hay độ sâu phân tích. Không tự áp dựa vào chi phí. Mỗi tối ưu được đánh giá cùng chất lượng và thời gian chủ phải sửa, không lấy giảm token làm tiêu chí duy nhất.

## 6. Giao diện và ví dụ sử dụng

### 6.1 Luồng thông thường

1. Chủ giao “Viết bài giới thiệu sản phẩm theo bảng giá này”. Mẫu đã chọn điền sản phẩm, nguồn giá, điều kiện và người xác nhận; nếu chưa có mẫu thì dùng fallback.
2. Team lập kế hoạch, làm và kiểm chứng bằng công cụ theo quyền hiện tại. Hợp đồng đã theo việc, chủ không cần mở thêm một form bắt buộc cho từng bước.
3. Thẻ kết quả ghi sản phẩm, nguồn, tiêu chí đạt/chưa đạt và phần cần chủ đánh giá. Không dùng “Xong” chỉ vì trưởng nhóm nói đã xong.
4. Chủ bấm Nhận kết quả hoặc Yêu cầu sửa trên đúng bản đang xem. Chi phí được hiển thị gọn, mở chi tiết khi cần.

### 6.2 Câu chữ bắt buộc nhất quán

- Phân biệt “Duyệt hành động trước khi làm” và “Nghiệm thu kết quả”.
- Các nhãn: Đang làm, Đang kiểm sản phẩm, Cần sửa, Chờ anh nghiệm thu, Đã nghiệm thu, Bản hiện tại cần kiểm lại.
- Chi phí chưa rõ: “Chưa đủ dữ liệu chi phí”, không “0 USD”. Vượt mức tham khảo: “Mức dùng cao hơn tham khảo; nhóm vẫn tiếp tục làm”.
- Thiếu nguồn: nêu nguồn nào thiếu và kết luận nào chưa kiểm được. Kết nối MCP khỏe không được hiển thị là dữ liệu nghiệp vụ đã chính xác.
- Không đưa tên bảng dữ liệu, khóa hash hay mã lỗi nội bộ vào luồng chính; phần bằng chứng/chi tiết có thể xem và xuất.
- Hỗ trợ tiếng Việt/Anh, màn hẹp, thử lại sau mất mạng; quyết định lấy trạng thái từ server.

## 7. Ranh giới dữ liệu và API dự kiến

Đây là hợp đồng thiết kế; tên bảng mới và migration được chốt trong kế hoạch kỹ thuật trên main mới. Không thay nghĩa API cũ một cách ngầm định.

| Bề mặt | Thay đổi cần có |
|---|---|
| `team_acceptance`, lịch sử checks | Thêm version/hash hợp đồng, nguồn mẫu, target message/manifest, evidence phiên bản và trạng thái validity |
| `tasks` làn Team | Dấu readiness hợp đồng trước claim; việc cũ chưa có hợp đồng được đối soát trước khi chạy |
| Mẫu nghiệm thu theo nhóm | Lưu version, điều kiện áp dụng, criteria, outputs, source requirements, quyền thay đổi chỉ của chủ |
| Usage event/parser/index | Định danh host, provenance, billing basis, độ bao phủ, supersedes/reconciliation; summary sử dụng cùng projection |
| Chính sách nhóm | Bật quality_first, mức tham khảo tùy chọn, trạng thái chuyển các trần cũ và nguồn của cấu hình |

API mở rộng:

- `GET/PUT /teams/{id}/acceptance-template`: xem/sửa mẫu mặc định đã version; sửa cần `expected_version`.
- Đường tạo việc đang có nhận thêm hợp đồng/mẫu tùy chọn; không truyền vẫn được host gắn fallback, không trả lỗi thiếu form.
- Giữ các route nghiệm thu hiện có; bổ sung contract_version, output_manifest_hash và decision_key khi quyết. Trong quality_first, client cũ thiếu khóa bản đang xem phải làm mới trước khi quyết, không suy đoán xác nhận bản mới nhất.
- `GET /teams/{id}/tasks/{task_id}` trả hợp đồng tóm tắt, acceptance status, completion_valid, reason và usage tóm tắt.
- `GET /teams/{id}/tasks/{task_id}/usage` và `GET /teams/{id}/usage`: xem số đo và độ bao phủ, không gây lời gọi model.
- `PUT /teams/{id}/quality-policy`: chủ đổi chính sách; agent không có directive tự đổi mode hay mức tham khảo.
- Giữ auth/CSRF và kiểm brain/team/task của các route hiện tại. Thiết lập nghiệm thu và mức dùng không cấp thêm quyền công cụ.

Các thao tác đổi tiêu chí, xác nhận, sửa yêu cầu, hủy việc và áp kết quả đều kiểm phiên bản dưới cơ chế khóa/CAS hiện tại. I/O đọc file, checker và tính usage nặng chạy ngoài khóa chung; khi trả về phải so lại snapshot trước khi áp.

## 8. P2: pilot nghiệp vụ có đối chứng

### 8.1 Phạm vi và dữ liệu

Thời gian theo dõi dự kiến 7-10 ngày, **20 cặp công việc = 40 lần thực hiện**, chia 10 cặp nội dung và 10 cặp phân tích bán hàng. Một cặp gồm cùng đề bài chạy với Team và một trợ lý. Mỗi team tối đa ba vai: điều phối, thực hiện, kiểm chứng. Không tự đổi model để làm bên nào rẻ hơn.

| Nghiệp vụ | Đầu vào chốt trước | Kiểm khách quan | Chủ đánh giá |
|---|---|---|---|
| Nội dung sản phẩm | Hồ sơ sản phẩm, bảng giá, điều cấm và yêu cầu bài | SKU/giá/khuyến mại đúng bản nguồn, đủ trường bắt buộc; không có tuyên bố công dụng ngoài dữ liệu cho phép | Thông điệp, độ rõ ràng, giọng viết, khả năng dùng ngay |
| Phân tích bán hàng | CSV hoặc bản xuất POS đã chốt kỳ, quy tắc hoàn/hủy/chiết khấu và đáp án tính độc lập | Tổng doanh thu, đơn, kỳ so sánh, công thức và sai số theo đơn vị dữ liệu; xử lý hoàn/hủy đúng | Lập luận, liên hệ số liệu, giá trị kiến nghị |

Nội dung không thể kiểm chắc bằng máy thì để chủ/kiểm chứng đánh giá, không tuyên bố checker đã phát hiện mọi công dụng bịa. Bộ đáp án số liệu được tạo độc lập với người/agent làm báo cáo, chủ chốt trước khi chạy.

Nếu chưa có nguồn POS, dùng bản xuất thật đã được chủ cung cấp và chốt phiên bản; không bịa dữ liệu để gọi là thử nghiệp vụ thật. Có thể dùng fixture tổng hợp cho kiểm kỹ thuật trước đó nhưng phải báo riêng. Dữ liệu nhạy cảm chỉ ở môi trường đã cho phép, không chép nguyên vào usage log hoặc tài liệu công khai.

### 8.2 Điều kiện so sánh công bằng

- Cùng đầu vào, quyền công cụ, tiêu chí nghiệm thu, freshness, model và mức suy luận đã chốt. Khác biệt chính là cách tổ chức một agent so với Team.
- Hai bên đều được làm tới yêu cầu, không đặt ngân sách token/tiền thấp để ép kết quả. Ghi lại nguồn lực thực đã dùng.
- Đóng băng bộ đề, rubric và cấu hình trước đợt đo; xáo thứ tự chạy, đổi nhãn kết quả khi chủ chấm nếu làm được.
- Tách lịch sử, workspace và bài học của hai nhánh; không cho bên chạy sau đọc đáp án hoặc sản phẩm bên trước. Trong đợt đo chính không tự học chéo từ bài chấm. Thử khả năng học làm đợt riêng.
- Không loại bỏ ca thất bại, hết quota, thiếu nguồn hoặc người dùng chưa duyệt. Báo loại nguyên nhân và các ca chưa có cặp hoàn chỉnh; nếu không đủ 20 cặp thì kéo dài hoặc kết luận chưa đủ mẫu.
- Trong cùng đợt chỉ dùng một bản build. Sửa lỗi làm thay hành vi thì kết thúc đợt cũ, giữ bằng chứng và chạy đợt mới; không gộp số tùy ý.

### 8.3 Chỉ số và điều kiện quyết định

| Chỉ số | Cách đo | Điều kiện đề xuất |
|---|---|---|
| Hoàn thành sai | Done khi chưa đạt/chưa xác nhận hoặc dùng evidence sai bản | 0 ca |
| Hành động ngoài không đúng quyền hoặc gửi trùng | Đối soát trace với audit công cụ | 0 ca |
| Chất lượng | Chủ chấm 0-2 mỗi trục: đúng dữ kiện, đầy đủ, hữu ích, rõ ràng; lỗi nghiêm trọng được ghi riêng | Team không thấp hơn một trợ lý về trung vị và số kết quả được chấp nhận, xem riêng từng nghiệp vụ |
| Lỗi nghiêm trọng | Sai giá/chính sách quan trọng, sai số tiền đáng kể theo quy tắc dữ liệu, kết luận không có nguồn | Không được bù bằng điểm văn phong; điều tra từng ca trước mở rộng |
| Công chủ phải làm | Phút giao thêm hướng dẫn, kiểm, sửa, quyết định; loại thời gian làm việc khác | Mục tiêu giảm trung vị 30% trong các cặp có thời gian baseline > 0; báo cả số phút tuyệt đối |
| Thời gian hoàn thành | Từ nhận việc đến sản phẩm được chấp nhận; tách chạy, chờ chủ, chờ nguồn/provider | Báo trung vị và các ca chậm nhất; không che thời gian chờ |
| Sửa lại | Đạt ngay lần đầu, số vòng sửa, việc chưa đạt | Báo cả tỷ lệ và số ca, không chỉ phần thành công |
| Chi phí | API provider báo/ước tính tách riêng, token trong gói, chi phí lần lỗi và chi phí chung | Chỉ số mô tả và giá trị trao đổi, không phải ngưỡng tiền loại Team |
| Bao phủ đo lường | Mỗi attempt có định danh và trạng thái known/unknown; usage đối soát có nguồn | 100% attempt được liệt kê; không yêu cầu provider cung cấp token mà họ không có |

20 cặp là thử nghiệm thăm dò, không chứng minh hiệu quả cho mọi loại việc. Nếu Team tốn hơn nhưng giảm công hoặc tăng chất lượng có giá trị, trình bày phần đánh đổi để chủ quyết. Nếu chất lượng tương đương mà tốn hơn, chậm hơn và không giảm công thì không mở rộng nghiệp vụ đó. Không hạ model hay tiêu chí để làm đẹp số tiết kiệm.

Chi phí trên mỗi kết quả được chấp nhận phải gồm cả các lần thất bại/sửa của cùng nhánh trong đợt. Chưa có kết quả được chấp nhận thì ghi không xác định, không chia cho 0 hoặc báo chi phí 0. Phần chi phí không quan sát được phải theo cùng báo cáo, không biến tỷ số phần đã biết thành tổng.

### 8.4 Vận hành và điều kiện dừng pilot

Tận dụng `pilot/team-multiday/` và `trace_export.py`, bổ sung contract/evidence version, usage provenance và bảng cặp đối chứng. Dùng state/brain riêng, fixture và checker được bảo vệ, không ghi lên dữ liệu kinh doanh gốc.

- Không gửi cho khách, đăng bài hay đặt hàng; chỉ bàn giao nháp và báo cáo cho chủ.
- Sau mỗi đợt lưu build, cấu hình, bộ đề/hash, trace, sản phẩm, kiểm tra, chấm của chủ và thời gian thao tác. Báo cáo gốc giữ nguyên, sửa kết luận bằng hậu kiểm riêng.
- Dừng nhánh có ghi nhầm dữ liệu gốc, vượt quyền, lộ dữ liệu, gọi lặp không tiến triển hoặc nghiệm thu sai; giữ toàn bộ bằng chứng, không đánh dấu việc đạt.
- Hết quota hay engine không hoạt động: ghi lỗi kỹ thuật và tiếp tục sau khi provider phục hồi, không tự chuyển engine để lấp số.
- Chi phí cao hay vượt mức tham khảo không làm pilot tự dừng. Chủ có thể dừng bất cứ lúc nào, ghi là dừng bởi chủ.
- Việc được chủ duyệt hay cấu hình model trong tài liệu không thay thế quyền thực thi. Spec này không tự khởi chạy pilot/model hay kết nối kênh ngoài.

## 9. Kiểm thử bắt buộc và điều kiện phát hành

Kiểm tự động dùng engine giả và state tạm. Chỉ thử model thật ở đợt nghiệp vụ đã chuẩn bị; không trộn hóa đơn thật vào bộ kiểm kỹ thuật.

| Mã | Ca cần chứng minh | Kết quả bắt buộc |
|---|---|---|
| A01 | Tạo việc từ mọi cửa: chat, tool, admin, mandate, API | Có hợp đồng trước khi dispatcher claim |
| A02 | Không có mẫu hoặc tiêu chí | Gắn owner_confirm cho kết quả cuối; vẫn thực hiện; không done trước xác nhận |
| A03 | Crash giữa tạo task, ghi contract và ready | Sau restart không có việc chạy lọt; retry không tạo trùng |
| A04 | Agent nói Xong, tự xóa tiêu chí hoặc tự nhận đã duyệt | Không thay hợp đồng, không tự đạt |
| A05 | File/JSON có hình thức đúng nhưng số liệu sai | Checker nghiệp vụ fail; nhãn không nói đã kiểm đúng nội dung |
| A06 | Kết quả chữ được xác nhận rồi tin/file đổi | Hash cũ không xác nhận bản mới; completion_valid=false khi phát hiện drift |
| A07 | Sửa yêu cầu, mẫu, hợp đồng hoặc nguồn khi đang kiểm | Kết quả cũ không được áp; mẫu không đổi hồi tố vào việc khác |
| A08 | Hai tab xác nhận, gửi lại sau mất mạng | Cùng decision_key idempotent; bản cũ bị từ chối; một tín hiệu nghiệm thu |
| A09 | Checker lỗi, unknown, file quá lớn hoặc không đọc được | Chờ chủ với lý do, không pass theo mặc định |
| A10 | Hai vòng sửa không đạt | Cần đổi cách xử lý, không done và không báo hết ngân sách |
| A11 | Windows/Linux: đường tuyệt đối, đường có gốc, symlink/junction, traversal | Không đọc ngoài phạm vi; không phụ thuộc quyền tạo symlink để bỏ qua các ca kiểm khác |
| A12 | Maker thay checker hoặc fixture | Thay đổi bị chặn hoặc làm bằng chứng unknown; không tự pass với checker mới |
| C01 | Usage API nhiều tool rounds trong một Team turn | Phân biệt đúng request và turn, có liên kết việc |
| C02 | CLI tổng cuối lượt rồi log chi tiết đến sau, replay/restart | Một khoản chỉ tính một lần, projection đối soát được |
| C03 | API thiếu giá/usage, gói thuê bao, CLI có giá quy đổi | Không giả miễn phí, không trộn tiền API và giá trị tham khảo |
| C04 | Retry thật, lỗi/hủy sau mốc model | Giữ usage thật và attempt, không bị chống trùng nhầm hoặc xóa chi phí |
| C05 | Vượt mức tham khảo, mất telemetry, giá chưa biết | Việc vẫn tiếp tục, model/độ sâu/kiểm chứng không đổi |
| C06 | Quality_first đi qua trần lượt cũ và agent budget | Preset tiết kiệm không chặn nghĩa vụ; giới hạn quản trị/quyền vẫn có hiệu lực và lý do đúng |
| C07 | Lượt chung của nhóm, mirror agent_calls/team_turns | Không cộng trùng, không phân bổ giả cho nhiều việc |
| C08 | Cache khác brain/quyền/nguồn/version, hành động có tác động | Không reuse sai dữ liệu hoặc replay hành động |
| O01 | Không tiến triển, mất mạng, provider chặn, restart, chủ hủy | Có kết cục rõ, giữ sản phẩm, không tự hạ chất lượng |
| P01 | Chạy thử khô bộ đo pilot với engine giả | 20 cặp và trạng thái đủ, không dựng engine thật |
| P02 | Bộ chấm, dữ liệu, lịch sử hai nhánh | Đầu vào bằng nhau, đáp án độc lập, không rò sản phẩm giữa hai nhánh |
| U01 | Điện thoại/desktop, vi/en, retry, nhiều tab | Nhãn đúng trạng thái server, không tràn ngang, không bắt duyệt chi phí |

Kiểm hồi quy liên quan: Team runner/store/API, approval/gate, mandate, usage API/CLI/index, turn_context, quyền model, migration và dashboard. Các gate sản phẩm không được tắt để cô lập môi trường test.

Điều kiện phát hành từng mốc: ca bắt buộc của mốc đạt, các lỗi nghiêm trọng được giải quyết, dữ liệu migration/restart được kiểm và đường quay về đã thử trên bản sao. Không dùng test giả để tuyên bố hiệu quả nghiệp vụ.

## 10. Migration, rollout và quay về

- Schema thêm theo cách idempotent, snapshot trước khi nâng; bảo toàn kho và nhật ký cũ.
- Việc đã xong trước nâng: gắn nhãn legacy/unverified nếu chưa có hợp đồng, không tự sửa lịch sử thành đã nghiệm thu, không tự chạy lại hành động.
- Việc đang chạy: không đổi luật giữa một bước đang thực thi; sau khi bước trả về và trước đóng việc phải có fallback hoặc hợp đồng chủ đặt, rồi mới kiểm. Việc cũ đang chờ được chuẩn bị trước lần claim kế tiếp.
- Việc có hợp đồng do chủ đặt giữ nguyên tiêu chí; không bị mẫu mới thay thế.
- Mốc mới bật trước trên nhóm pilot bằng chính sách từng nhóm. Chỉ bật mặc định cho nhóm mới sau khi P0/P1 đạt và pilot có kết luận. Chuyển nhóm cũ là lựa chọn rõ ràng, giữ lịch sử chính sách và xử lý trần cũ như mục 5.3.
- Tắt tính năng cho nhóm mới không được làm mất hợp đồng đã gắn cho việc đang mở. Không downgrade bản cũ vào state mới rồi tiếp tục dispatch: bản cũ có thể bỏ qua chốt nghiệm thu/readiness.
- Quay về: tạm dừng Team dispatcher và hoạt động chủ động, dừng tiến trình, sao lưu, phục hồi bộ state nhất quán trước nâng cùng build tương ứng trên môi trường thử trước. Không chỉ khôi phục một kho hoặc chép DB đang mở. Giữ sản phẩm và dấu vết sau nâng để đối chiếu thủ công; không replay hành động ngoài vì rollback.

## 11. Các mốc triển khai và trách nhiệm

| Mốc | Sản phẩm phải bàn giao | Phụ thuộc | Ước lượng sơ bộ |
|---|---|---|---|
| M0 | Cô lập test factory khỏi config máy, sửa kiểm đường dẫn, thay kiểm thời gian mong manh bằng đồng bộ xác định | Nền main mới | 1-2 ngày phát triển |
| M1 | Hợp đồng mặc định, template, readiness, xác nhận gắn phiên bản, trạng thái/UI/migration | M0 | 4-6 ngày phát triển |
| M2 | Usage liên kết, provenance/coverage, observe_only, chuyển preset trần cũ, cảnh báo không chặn | M0; cùng định danh task/turn của M1 | 2-4 ngày phát triển |
| M3 | Hai mẫu nghiệp vụ, dữ liệu và đáp án chốt, 20 cặp/40 lần, báo cáo so sánh | M1/M2 đạt, nguồn nghiệp vụ sẵn | 7-10 ngày theo dõi |
| M4 | Kết luận từng nghiệp vụ, tối ưu có bằng chứng, mở mặc định nhóm mới nếu đạt | M3 | Lập theo kết quả, không ấn định trước |

M1 và M2 là hai phần triển khai có thể review riêng nhưng phải dùng chung quy ước revision/turn/usage. Không mở rộng pilot thật khi M1 chưa bảo đảm nghiệm thu hoặc M2 chưa phân biệt số đo biết/chưa biết. Tổng 3-4 tuần là dự kiến với một người triển khai chính và chủ có thời gian chấm, không là cam kết.

- Người triển khai: mã, test, migration, số đo và tài liệu vận hành.
- Chủ sản phẩm: tiêu chí nội dung/kinh doanh, nguồn dữ liệu, lựa chọn model, chấm kết quả và quyết định giá trị trao đổi chất lượng/thời gian/chi phí.
- Agent kiểm chứng: thu bằng chứng và chỉ ra vấn đề; không thay quyền nghiệm thu của chủ.

Khi chuyển sang implementation, lập kế hoạch kỹ thuật riêng cho M1 và M2 cùng hợp đồng tích hợp, theo quy ước nhánh/version/CI của repo. Không dự trữ phiên bản phát hành hay thay VERSION chỉ vì viết spec.

## 12. Tiêu chuẩn thành công của toàn bộ đợt

1. Mọi việc thuộc chính sách mới có hợp đồng trước thực thi và không báo đã nghiệm thu khi chưa đủ bằng chứng đúng bản.
2. Chi phí được trình bày trung thực, bao gồm phần chưa biết; việc không bị hạ chất lượng hay chặn chỉ vì chi phí cao.
3. Hạn mức cũ được xử lý rõ, không có trần tiết kiệm ẩn chặn nghĩa vụ dưới nhãn quality_first.
4. Có bộ bằng chứng pilot đủ để quyết theo từng nghiệp vụ; không kết luận chung từ test giả hoặc chỉ từ số token giảm.
5. Những quy trình Team không tạo thêm giá trị được giữ ở một trợ lý/workflow phù hợp; những quy trình có giá trị được đóng gói cùng mẫu nghiệm thu và cách đo.

## 13. Nguồn đối chiếu và tự rà soát

Các đường dưới đây chỉ mã trên nền d6781404, không tuyên bố nhánh hiện tại đã có thay đổi mới:

- `server/team_runner.py`: `_finish`, `_apply_checks_locked`, `set_acceptance`, `decide_acceptance`, giới hạn lượt và luồng gọi engine.
- `server/team_acceptance.py`: kinds hiện tại là `json_keys`, không phải JSON Schema đầy đủ; evidence, vòng kiểm, stale.
- `server/team_store.py`, `server/team_api.py`, `server/team_mandate.py`: hàng đợi, cửa tạo việc, template pilot hiện chưa tự áp.
- `server/agent_ledger.py`, `server/turn_context.py`: mirror lượt và danh tính do host cấp.
- `server/usage_store.py`, `server/usage_index.py`, `server/usage_parsers.py`, `server/aux_engine.py`, `server/main.py`: nguồn usage API/CLI và nguy cơ cộng trùng.
- `pilot/team-multiday/{README.txt,metrics.txt,config/teams.json,trace_export.py}`: bộ pilot tái sử dụng.
- `exports/team-swot-20261022-d6781404/{team-test-results.txt,gate-isolated-results.txt,sequential-slower-fake-results.txt}`: kiểm local và chẩn đoán, không phải số đo model thật mới.

Tự rà soát thiết kế: đã phân biệt tiền/usage trong gói, request/lượt, nghiệm thu/quyền hành động, kết quả cũ/bản hiện tại; có xử lý hai kho không nguyên tử, dữ liệu cũ, giới hạn cũ, mất telemetry, chi phí chưa biết và pilot thiếu cặp. Không có chỗ mặc định lấy tiết kiệm làm lý do hạ tiêu chuẩn hoặc tuyên bố đã chạy nghiệp vụ.
