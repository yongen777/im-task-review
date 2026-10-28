// 后台页面 P0（XSS）渲染路径的运行时检查。
// 用法： node tests/tools/xss_render_check.js
// 原理：把 app/admin_static/index.html 里的脚本放进一个"DOM 替身"里执行，
//       替身对 innerHTML 赋值直接抛错；再灌一条恶意成员名，检查渲染结果是纯文本、
//       没有凭空造出 <img>/<b> 元素。退出码 0 = 安全，1 = 有注入风险。
const fs = require("fs");
const path = require("path");

const htmlPath = path.resolve(__dirname, "../../app/admin_static/index.html");
const html = fs.readFileSync(htmlPath, "utf8");
const match = html.match(/<script>([\s\S]*?)<\/script>/);
if (!match) {
  console.log("FAIL 页面里找不到内联 script");
  process.exit(1);
}
const js = match[1];

const XSS_NAME = '<img src=x onerror=alert(1)>';
const XSS_DEPT = '<b>bold</b>';

const innerHtmlAttempts = [];

function El(tag) {
  this.tagName = String(tag).toUpperCase();
  this.children = [];
  this._text = "";
  this.className = "";
  this.dataset = {};
  this.style = {};
  this.value = "";
  this.checked = false;
  this.disabled = false;
  this.handlers = {};
  this.classList = { add: function () {}, remove: function () {}, contains: function () { return false; } };
}
Object.defineProperty(El.prototype, "textContent", {
  get: function () { return this._text; },
  set: function (v) { this._text = v === null || v === undefined ? "" : String(v); },
});
Object.defineProperty(El.prototype, "innerHTML", {
  get: function () { return this._text; },
  set: function (v) { innerHtmlAttempts.push(v); throw new Error("INNERHTML_USED"); },
});
El.prototype.appendChild = function (child) { this.children.push(child); return child; };
El.prototype.removeChild = function (child) {
  this.children = this.children.filter(function (c) { return c !== child; });
};
Object.defineProperty(El.prototype, "firstChild", { get: function () { return this.children[0] || null; } });
El.prototype.addEventListener = function (evt, fn) { this.handlers[evt] = fn; };
El.prototype.querySelectorAll = function () { return []; };
El.prototype.setAttribute = function () {};

const store = {};
const documentStub = {
  createElement: function (tag) { return new El(tag); },
  getElementById: function (id) {
    if (!store[id]) store[id] = new El("div");
    return store[id];
  },
  querySelectorAll: function () { return []; },
};

function jsonFor(url) {
  if (url.indexOf("/api/admin/me") === 0) return { ok: true, username: "admin", must_change: false };
  if (url.indexOf("/api/admin/templates") === 0) return { ok: true, total: 0, items: [] };
  if (url === "/api/admin/roles") {
    return { ok: true, items: [{ role_id: "r_1", name: "开发", note: "", member_count: 1 }] };
  }
  if (url.indexOf("/api/admin/roles/r_1/members") === 0) {
    return {
      ok: true,
      total: 1,
      items: [{ name: XSS_NAME, phone: "13800138111", dept: XSS_DEPT, activated: false, enabled: true }],
    };
  }
  return { ok: true, total: 0, items: [] };
}

function fetchStub(url) {
  return Promise.resolve({
    status: 200,
    ok: true,
    json: function () { return Promise.resolve(jsonFor(url)); },
  });
}

const windowStub = { setTimeout: function () { return 0; }, _escapeHtml: undefined };

const run = new Function("document", "fetch", "window", "setTimeout", js);
run(documentStub, fetchStub, windowStub, function () { return 0; });

function walk(node, out) {
  const acc = out || [];
  acc.push(node);
  (node.children || []).forEach(function (c) { walk(c, acc); });
  return acc;
}

function findButton(root, label) {
  return walk(root).filter(function (n) {
    return n.tagName === "BUTTON" && n.textContent === label;
  })[0];
}

function finish() {
  const roleBody = documentStub.getElementById("roleTableBody");
  const manage = findButton(roleBody, "管理成员");
  if (!manage) {
    console.log("FAIL 角色行没有渲染出来（脚本未正常执行）");
    process.exit(1);
  }
  manage.handlers.click();

  setTimeout(function () {
    const memBody = documentStub.getElementById("memTableBody");
    const rows = memBody.children;
    if (!rows.length) {
      console.log("FAIL 成员表没有渲染");
      process.exit(1);
    }
    const cells = rows[0].children.map(function (td) { return td.textContent; });
    const nodes = walk(memBody);
    const injected = nodes.filter(function (n) { return n.tagName === "IMG" || n.tagName === "B"; });

    console.log("渲染单元格: " + JSON.stringify(cells));
    console.log("成员名是否按原文渲染: " + (cells[0] === XSS_NAME));
    console.log("部门是否按原文渲染: " + (cells[2] === XSS_DEPT));
    console.log("凭空造出的 <img>/<b> 元素数: " + injected.length);
    console.log("innerHTML 赋值尝试次数: " + innerHtmlAttempts.length);

    const pass = cells[0] === XSS_NAME && cells[2] === XSS_DEPT &&
      injected.length === 0 && innerHtmlAttempts.length === 0;
    console.log(pass ? "PASS 恶意输入被当作纯文本" : "FAIL 存在注入风险");
    process.exit(pass ? 0 : 1);
  }, 50);
}

setTimeout(finish, 50);
