/* Menu "+" của khung gõ (0.153.0): sổ đăng ký, lọc thư mục, hàng đợi tải lên.

       node tests/js/test_menu_cong.js
*/
const fs = require("fs");
const path = require("path");
const P = require("../../dashboard/composer-plus.js");
const ROOT = path.join(__dirname, "../..");
const src = fs.readFileSync(path.join(ROOT, "dashboard/composer-plus.js"), "utf8");
const app = fs.readFileSync(path.join(ROOT, "dashboard/app.js"), "utf8");
const html = fs.readFileSync(path.join(ROOT, "dashboard/index.html"), "utf8");
let fails = [];
function check(name, cond) { console.log((cond ? "ok   " : "FAIL ") + name); if (!cond) fails.push(name); }

const reg = P.createRegistry();
reg.register({ id: "b", order: 20, label: "x.b" });
reg.register({ id: "a", order: 10, label: "x.a" });
reg.register({ id: "b", order: 5, label: "x.b2" });
check("sổ đăng ký xếp theo order, đăng ký lại cùng id thì thay", reg.list().map(i => i.id + ":" + i.label).join(",") === "b:x.b2,a:x.a");

const f = (rel) => ({ name: rel.split("/").pop(), webkitRelativePath: rel });
let r = P.pickFolderFiles([f("du-an/a.md"), f("du-an/.env"), f("du-an/.git/config"), f("du-an/node_modules/x/i.js"), f("du-an/con/b.csv")]);
check("thư mục: bỏ file ẩn, .git, node_modules", r.files.map(x => x.name).join(",") === "a.md,b.csv" && r.skipped === 3 && r.over === 0);
const many = []; for (let i = 0; i < 61; i++) many.push(f("d/" + i + ".txt"));
r = P.pickFolderFiles(many);
check("thư mục: trần " + P.MAX_FOLDER_FILES + " file, báo số dư", r.files.length === P.MAX_FOLDER_FILES && r.over === 11);
r = P.pickFolderFiles([f("d/.DS_Store")]);
check("thư mục toàn file ẩn: không còn file nào", r.files.length === 0 && r.skipped === 1);

(async () => {
  let dang = 0, max = 0, xong = [];
  await P.runQueue([1, 2, 3, 4, 5, 6, 7, 8, 9], async (x) => {
    dang++; max = Math.max(max, dang); await new Promise(res => setTimeout(res, 5)); dang--; xong.push(x);
    if (x === 3) throw new Error("hỏng");
  }, 4);
  check("hàng đợi: tối đa 4 file cùng lúc, file hỏng không chặn file sau", max === 4 && xong.length === 9);

  // Thư mục: mọi file vào danh sách đính kèm TRƯỚC khi tải file đầu (gửi không thiếu file),
  // và dòng ghi chú (bỏ file ẩn, quá 50 file) không bị lần tải đầu xoá mất.
  const tw = (k, v) => k + (v ? JSON.stringify(v) : "");
  const goi = [];
  const stub = {
    queue: (files, note) => { goi.push(["queue", files.length, note]); return files.map((f, i) => ({ f, id: i })); },
    upload: (f, att) => { goi.push(["upload", f.name, att && att.f === f]); return Promise.resolve(); },
    note: (s) => goi.push(["note", s]),
  };
  const nhieu = []; for (let i = 0; i < 61; i++) nhieu.push(f("d/" + i + ".txt"));
  nhieu.push(f("d/.env"), f("d/.git/HEAD"), f("d/node_modules/a.js"));
  await P.handleFolder(nhieu, stub, tw);
  check("thư mục: xếp hàng cả 50 file một lần, trước lần tải đầu", goi[0][0] === "queue" && goi[0][1] === 50);
  check("thư mục: ghi chú nói rõ bỏ file ẩn và quá 50 file", /folder_skipped/.test(goi[0][2]) && /folder_over/.test(goi[0][2]));
  const tai = goi.filter(g => g[0] === "upload");
  check("thư mục: tải đúng 50 file, mỗi file gắn đúng chip đã xếp hàng", tai.length === 50 && tai.every(g => g[2] === true));
  check("thư mục: không gọi note riêng làm lần tải đầu xoá ghi chú", !goi.some(g => g[0] === "note"));
  goi.length = 0;
  await P.handleFolder([f("d/.DS_Store")], stub, tw);
  check("thư mục toàn file ẩn: không xếp hàng, chỉ báo", !goi.some(g => g[0] === "queue") && goi.some(g => g[0] === "note" && /folder_empty/.test(g[1])));

  check("mũi tên trái trong ô tìm không thoát ô tìm", P.shouldGoBack({ key: "ArrowLeft", target: { tagName: "INPUT" } }, true) === false);
  check("mũi tên trái trên một dòng menu con thì quay lại", P.shouldGoBack({ key: "ArrowLeft", target: { tagName: "BUTTON" } }, true) === true);
  check("menu gốc không có nút quay lại thì không làm gì", P.shouldGoBack({ key: "ArrowLeft", target: { tagName: "BUTTON" } }, false) === false);

  const anh = P.brainAttachment({ ok: true, path: "D:/b/attachments/x.jpg", name: "x.jpg", size: 9, kind: "image" }, "attachments/x.jpg", "D:/b");
  check("ảnh chọn từ brain có url xem được (không thành 'hết hạn')", anh.url === "/files/raw?brain=" + encodeURIComponent("D:/b") + "&path=" + encodeURIComponent("attachments/x.jpg"));
  check("file thường chọn từ brain không cần url", !P.brainAttachment({ ok: true, path: "D:/b/a.md", name: "a.md", kind: "file" }, "a.md", "D:/b").url);
  check("app.js giữ url của file chọn từ brain", /addBrainFile[\s\S]{0,400}url: it\.url/.test(app));
  check("app.js có JavisAttach.queue xếp hàng file trước khi tải", /window\.JavisAttach = \{[\s\S]*queue:/.test(app));
  check("uploadFile giải lời hứa chờ của chip đã xếp hàng", app.indexOf("att._cho") !== -1);
  check("uploadFile không xoá ghi chú của chip xếp hàng từ thư mục", /if \(!att\._hangDoi\) attachNote = ""/.test(app));
  check("tìm file trong brain hỏng thì báo, không đóng lặng lẽ", src.indexOf('tw("cp.brain_failed")') !== -1);

  ["attach", "folder", "brain_file", "commands", "connections", "partners"].forEach(id =>
    check("mục mặc định có id " + id, src.indexOf('id: "' + id + '"') !== -1));
  check("Ctrl+U chặn mặc định của trình duyệt (xem mã nguồn)", /key === "u"[\s\S]{0,400}preventDefault/.test(src));
  check("mục lệnh mở menu / bằng sự kiện input", src.indexOf('new Event("input"') !== -1);
  check("gọi trợ lý chèn đúng token của menu /", src.indexOf('"/agent-" + ') !== -1 && src.indexOf('"/workflow-" + ') !== -1);
  check("app.js lộ JavisAttach (pick, upload, addBrainFile, note)", /window\.JavisAttach = \{[\s\S]*pick[\s\S]*upload[\s\S]*addBrainFile[\s\S]*note/.test(app));
  check("composer-plus.js nạp sau model-picker.js", html.indexOf("model-picker.js") < html.indexOf("composer-plus.js"));
  check("không chữ Việt trong mã chạy của composer-plus.js",
    !/[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]/i.test(src.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "")));
  if (fails.length) { console.log("\nFAIL - test_menu_cong: " + fails.length + " lỗi"); process.exit(1); }
  console.log("\nOK - test_menu_cong: tất cả pass");
})();
