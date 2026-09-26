#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内容管理后台系统 —— 单端口 HTTP 服务端

技术栈：Python 3 标准库（http.server / sqlite3 / hashlib / json），零第三方依赖。
数据持久化：SQLite（data/app.db，随应用在云端沙箱内持久保存）
文件存储：服务器端 uploads/ 目录，通过 /uploads/<name> 对外提供访问
"""

import os
import re
import sys
import json
import time
import uuid
import base64
import hashlib
import mimetypes
import secrets
import sqlite3
import threading
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

# --------------------------------------------------------------------------
# 路径与全局配置
# --------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "app.db")

for _d in (STATIC_DIR, UPLOAD_DIR, DATA_DIR):
    os.makedirs(_d, exist_ok=True)

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))

SESSION_DAYS = 7
MAX_UPLOAD = 12 * 1024 * 1024  # 12MB
PBKDF2_ROUNDS = 120_000

_db_lock = threading.Lock()


# --------------------------------------------------------------------------
# 数据库
# --------------------------------------------------------------------------
def now() -> dt.datetime:
    return dt.datetime.now()


def fmt(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def query(sql: str, args=(), one: bool = False):
    with _db_lock:
        conn = connect()
        try:
            cur = conn.execute(sql, args)
            rows = cur.fetchall()
            conn.commit()
            data = [dict(r) for r in rows]
            if one:
                return data[0] if data else None
            return data
        finally:
            conn.close()


def execute(sql: str, args=()):
    with _db_lock:
        conn = connect()
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            return cur.lastrowid, cur.rowcount
        finally:
            conn.close()


def hash_password(password: str, salt: str = None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ROUNDS)
    return base64.b64encode(dk).decode(), salt


def verify_password(password: str, hashed: str, salt: str) -> bool:
    calc, _ = hash_password(password, salt)
    return secrets.compare_digest(calc, hashed)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE,
  display_name  TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  salt          TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT 'editor',
  email         TEXT DEFAULT '',
  phone         TEXT DEFAULT '',
  avatar        TEXT DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'active',
  last_login    TEXT DEFAULT '',
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL UNIQUE,
  slug       TEXT DEFAULT '',
  color      TEXT DEFAULT '#4f6ef7',
  sort_order INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contents (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  title        TEXT NOT NULL,
  slug         TEXT DEFAULT '',
  category_id  INTEGER,
  tags         TEXT DEFAULT '',
  summary      TEXT DEFAULT '',
  body         TEXT DEFAULT '',
  cover        TEXT DEFAULT '',
  status       TEXT NOT NULL DEFAULT 'draft',
  author_id    INTEGER,
  author_name  TEXT DEFAULT '',
  views        INTEGER DEFAULT 0,
  likes        INTEGER DEFAULT 0,
  is_top       INTEGER DEFAULT 0,
  published_at TEXT DEFAULT '',
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  filename    TEXT NOT NULL,
  stored_name TEXT NOT NULL,
  mime        TEXT DEFAULT '',
  size        INTEGER DEFAULT 0,
  url         TEXT NOT NULL,
  uploader    TEXT DEFAULT '',
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER,
  username   TEXT DEFAULT '',
  action     TEXT DEFAULT '',
  target     TEXT DEFAULT '',
  detail     TEXT DEFAULT '',
  ip         TEXT DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contents_status ON contents(status);
CREATE INDEX IF NOT EXISTS idx_contents_cat ON contents(category_id);
CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(created_at);
"""


CATEGORY_SEED = [
    ("产品动态", "#4f6ef7", 1),
    ("技术分享", "#0ea5a5", 2),
    ("行业观察", "#f59e0b", 3),
    ("公司公告", "#8b5cf6", 4),
    ("用户故事", "#ec4899", 5),
]

TITLES = [
    ("全新管理工作台上线，效率提升 40%", 0, "published", 3820),
    ("v3.2 版本发布说明：批量操作与权限细化", 0, "published", 2140),
    ("从零搭建高可用内容中台的技术选型复盘", 1, "published", 5680),
    ("SQLite 在中小型业务中的真实表现", 1, "published", 3210),
    ("前端响应式布局的 7 个实用技巧", 1, "published", 4470),
    ("2026 内容运营行业趋势报告", 2, "published", 6930),
    ("AI 辅助内容生产，是效率工具还是新瓶颈？", 2, "published", 5120),
    ("中小团队如何搭建自己的内容审核流程", 2, "draft", 0),
    ("关于国庆假期服务保障安排的公告", 3, "published", 1890),
    ("平台数据安全合规升级通知", 3, "published", 2410),
    ("用户增长计划激励规则调整说明", 3, "draft", 0),
    ("某教育机构如何用 3 个月沉淀 500 篇内容", 4, "published", 7640),
    ("设计师眼中的后台界面：好看与好用的平衡", 4, "published", 3980),
    ("内容创作者访谈：坚持日更的第 300 天", 4, "published", 2870),
    ("图文编辑器升级：支持 Markdown 实时预览", 0, "published", 1520),
    ("内容标签体系的搭建与治理实践", 1, "archived", 980),
    ("多端自适应方案对比：栅格、流体与容器查询", 1, "draft", 0),
    ("内容冷启动阶段的选题方法论", 2, "published", 3350),
    ("关于新增数据看板模块的公告", 3, "draft", 0),
    ("老用户回访：他们为什么留下来", 4, "published", 2210),
    ("运营活动页搭建效率优化小结", 0, "published", 1760),
    ("把审核周期从 3 天缩短到 4 小时的实践", 2, "archived", 1240),
    ("内容归档规范 v1.0", 3, "published", 890),
    ("深色模式适配踩坑记录", 1, "draft", 0),
]

BODY_TEMPLATE = """## 背景

这是一篇示例正文，用于演示内容管理后台的编辑与预览能力。真实使用时，你可以直接在编辑器中替换为业务内容。

## 核心要点

- 内容与分类、标签、状态、封面等字段完整可维护
- 列表支持关键词检索、分类与状态筛选、分页与批量操作
- 表单支持实时校验、暂存草稿与发布流转

## 后续计划

| 阶段 | 目标 |
| --- | --- |
| 第一阶段 | 打通内容创建与发布链路 |
| 第二阶段 | 补充数据看板与运营分析 |
| 第三阶段 | 权限细化与审核工作流 |

## 结语

保持迭代，小步快跑。"""


def init_db():
    with _db_lock:
        conn = connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    if query("SELECT COUNT(*) AS c FROM users", one=True)["c"] == 0:
        seed()


def seed():
    t = now()
    # --- 用户 ---
    users = [
        ("admin", "超级管理员", "admin123", "admin", "admin@example.com", "13800000001"),
        ("editor", "内容编辑", "editor123", "editor", "editor@example.com", "13800000002"),
        ("lisi", "李思", "lisi123", "editor", "lisi@example.com", "13800000003"),
    ]
    for uname, dname, pwd, role, mail, phone in users:
        h, s = hash_password(pwd)
        execute(
            "INSERT INTO users(username,display_name,password_hash,salt,role,email,phone,status,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (uname, dname, h, s, role, mail, phone, "active", fmt(t - dt.timedelta(days=90))),
        )

    # --- 分类 ---
    for name, color, order in CATEGORY_SEED:
        execute(
            "INSERT INTO categories(name,slug,color,sort_order,created_at) VALUES(?,?,?,?,?)",
            (name, slugify(name), color, order, fmt(t)),
        )
    cats = query("SELECT id,name FROM categories ORDER BY sort_order")

    # --- 内容 ---
    admin_id = query("SELECT id FROM users WHERE username='admin'", one=True)["id"]
    for i, (title, cat_idx, status, views) in enumerate(TITLES):
        created = t - dt.timedelta(days=(len(TITLES) - i) * 2 + (i % 3))
        published = fmt(created + dt.timedelta(hours=6)) if status == "published" else ""
        tags = ",".join(secrets.SystemRandom().sample(
            ["运营", "增长", "设计", "技术", "复盘", "方法论", "公告", "数据"], 3))
        execute(
            "INSERT INTO contents(title,slug,category_id,tags,summary,body,cover,status,author_id,"
            "author_name,views,likes,is_top,published_at,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                title,
                slugify(title)[:60] + "-" + str(i + 1),
                cats[cat_idx]["id"],
                tags,
                title + "。这里是一段用于列表展示的内容摘要，简要说明本篇内容的核心信息与适用场景。",
                BODY_TEMPLATE,
                "assets/covers/c%d.svg" % ((i % 6) + 1),
                status,
                admin_id,
                "超级管理员",
                views,
                int(views / 12) if views else 0,
                1 if i in (0, 5) else 0,
                published,
                fmt(created),
                fmt(created + dt.timedelta(days=1)),
            ),
        )

    # --- 操作日志 ---
    actions = [("登录系统", "认证"), ("创建内容", "内容"), ("更新内容", "内容"),
               ("删除内容", "内容"), ("上传文件", "媒体"), ("修改分类", "分类")]
    for i in range(16):
        a, mod = actions[i % len(actions)]
        execute(
            "INSERT INTO logs(user_id,username,action,target,detail,ip,created_at) VALUES(?,?,?,?,?,?,?)",
            (admin_id, "admin", a, mod, "示例操作记录", "127.0.0.1",
             fmt(t - dt.timedelta(hours=i * 5, minutes=(i * 17) % 60))),
        )

    # --- 媒体库示例文件（云端文件存储演示） ---
    cover_dir = os.path.join(STATIC_DIR, "assets", "covers")
    for i in (1, 2, 3, 4):
        src = os.path.join(cover_dir, "c%d.svg" % i)
        if not os.path.isfile(src):
            continue
        with open(src, "rb") as fi:
            blob = fi.read()
        stored = "demo-cover-%d.svg" % i
        with open(os.path.join(UPLOAD_DIR, stored), "wb") as fo:
            fo.write(blob)
        execute(
            "INSERT INTO attachments(filename,stored_name,mime,size,url,uploader,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            ("示例封面 %d.svg" % i, stored, "image/svg+xml", len(blob),
             "/uploads/" + stored, "超级管理员", fmt(t - dt.timedelta(days=i))),
        )


def slugify(text: str) -> str:
    out = re.sub(r"[^\w\u4e00-\u9fa5]+", "-", text.strip().lower())
    return out.strip("-") or uuid.uuid4().hex[:8]


def log_action(user, action, target, detail=""):
    try:
        execute(
            "INSERT INTO logs(user_id,username,action,target,detail,ip,created_at) VALUES(?,?,?,?,?,?,?)",
            (user["id"] if user else None, user["username"] if user else "匿名",
             action, target, detail, "", fmt(now())),
        )
    except Exception:
        pass


# --------------------------------------------------------------------------
# 认证
# --------------------------------------------------------------------------
def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    t = now()
    execute("INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
            (token, user_id, fmt(t), fmt(t + dt.timedelta(days=SESSION_DAYS))))
    execute("DELETE FROM sessions WHERE expires_at < ?", (fmt(t),))
    return token


def resolve_token(handler):
    auth = handler.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        token = handler.headers.get("X-Token", "").strip()
    if not token:
        return None
    row = query(
        "SELECT s.token, s.expires_at, u.* FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token = ?", (token,), one=True)
    if not row:
        return None
    try:
        if dt.datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S") < now():
            execute("DELETE FROM sessions WHERE token=?", (token,))
            return None
    except Exception:
        return None
    if row.get("status") != "active":
        return None
    row.pop("password_hash", None)
    row.pop("salt", None)
    return row


def public_user(row: dict) -> dict:
    return {k: row.get(k) for k in
            ("id", "username", "display_name", "role", "email", "phone", "avatar",
             "status", "last_login", "created_at")}


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def page_params(qs):
    try:
        p = max(1, int(qs.get("page", ["1"])[0]))
    except Exception:
        p = 1
    try:
        s = min(100, max(1, int(qs.get("size", ["10"])[0])))
    except Exception:
        s = 10
    return p, s


def one(qs, key, default=""):
    v = qs.get(key, [default])
    return v[0] if v else default


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.msg = msg
        self.code = code


def parse_multipart(body: bytes, boundary: bytes):
    """极简 multipart/form-data 解析，返回 (fields, files)。"""
    fields, files = {}, {}
    delim = b"--" + boundary
    for seg in body.split(delim):
        if not seg or seg in (b"--", b"--\r\n"):
            continue
        if seg.startswith(b"--"):
            break
        if seg.startswith(b"\r\n"):
            seg = seg[2:]
        pos = seg.find(b"\r\n\r\n")
        if pos < 0:
            continue
        header_blob = seg[:pos].decode("utf-8", "replace")
        data = seg[pos + 4:]
        if data.endswith(b"\r\n"):
            data = data[:-2]
        name = fname = ctype = ""
        for line in header_blob.split("\r\n"):
            low = line.lower()
            if low.startswith("content-disposition"):
                m = re.search(r'name="([^"]*)"', line)
                if m:
                    name = m.group(1)
                m = re.search(r'filename="([^"]*)"', line)
                if m:
                    fname = m.group(1)
            elif low.startswith("content-type"):
                ctype = line.split(":", 1)[1].strip()
        if not name:
            continue
        if fname:
            files[name] = {"filename": fname, "content": data, "mime": ctype}
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files


SAFE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico",
            ".pdf", ".txt", ".md", ".csv", ".xlsx", ".docx", ".zip", ".mp4", ".mp3"}


# --------------------------------------------------------------------------
# 请求处理
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "AdminPanel/1.0"
    protocol_version = "HTTP/1.1"

    # ---------------- 基础输出 ----------------
    def _send(self, status, body: bytes, ctype="application/octet-stream", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if ctype.startswith("application/json")
                         else ("no-cache" if (ctype.startswith("text/html")
                                              or "javascript" in ctype or "css" in ctype)
                               else "public, max-age=3600"))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def json_out(self, data=None, code=0, msg="ok", status=200):
        payload = json.dumps({"code": code, "msg": msg, "data": data},
                             ensure_ascii=False).encode("utf-8")
        self._send(status, payload, "application/json; charset=utf-8")

    def fail(self, msg, status=400, code=None):
        body = json.dumps({"code": code if code is not None else status,
                           "msg": msg, "data": None}, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def log_message(self, fmt_str, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt_str % args))

    # ---------------- 请求体 ----------------
    def read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0:
            return b""
        if length > MAX_UPLOAD:
            raise ApiError("请求体过大", 413)
        data = self.rfile.read(length)
        self._consumed += len(data)
        return data

    def read_json(self) -> dict:
        raw = self.read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise ApiError("请求数据格式错误")

    # ---------------- HTTP 方法 ----------------
    def do_GET(self):
        self.dispatch("GET")

    def do_HEAD(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        self._consumed = 0
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                self.handle_api(method, path, qs)
            elif path.startswith("/uploads/"):
                self.serve_file(os.path.join(UPLOAD_DIR, os.path.basename(path)))
            elif path.startswith("/static/"):
                self.serve_file(os.path.join(STATIC_DIR, path[len("/static/"):].replace("/", os.sep)))
            else:
                self.serve_static(path)
        except ApiError as e:
            self.fail(e.msg, e.code)
        except Exception as e:  # noqa
            sys.stderr.write("ERROR %s %s -> %r\n" % (method, path, e))
            self.fail("服务器内部错误：%s" % e, 500)
        finally:
            # 排空未消费的请求体，避免 keep-alive 连接把剩余字节误解析为下一条请求
            try:
                cl = int(self.headers.get("Content-Length") or 0)
            except Exception:
                cl = 0
            remain = cl - self._consumed
            if remain > 0:
                if remain > 1024 * 1024:
                    self.close_connection = True
                else:
                    try:
                        self.rfile.read(remain)
                    except Exception:
                        self.close_connection = True

    # ---------------- 静态资源 ----------------
    def serve_static(self, path):
        if path in ("/", "/index.html"):
            return self.serve_file(os.path.join(STATIC_DIR, "index.html"))
        if path in ("/admin", "/admin/", "/dashboard"):
            return self.serve_file(os.path.join(STATIC_DIR, "admin.html"))
        rel = path.lstrip("/").replace("/", os.sep)
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if full.startswith(STATIC_DIR) and os.path.isfile(full):
            return self.serve_file(full)
        self.fail("页面不存在", 404)

    def serve_file(self, full):
        if not os.path.isfile(full):
            return self.fail("文件不存在", 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    # ======================================================================
    # API 路由
    # ======================================================================
    def handle_api(self, method, path, qs):
        parts = [p for p in path[len("/api/"):].split("/") if p]
        head = parts[0] if parts else ""
        rest = parts[1:]
        handler = getattr(self, "api_" + head.replace("-", "_"), None)
        if not handler:
            return self.fail("接口不存在", 404)
        return handler(method, rest, qs)

    # ---------------- 认证模块 ----------------
    def api_auth(self, method, rest, qs):
        action = rest[0] if rest else ""

        if action == "login" and method == "POST":
            data = self.read_json()
            username = (data.get("username") or "").strip()
            password = data.get("password") or ""
            if not username or not password:
                raise ApiError("请输入账号和密码")
            user = query("SELECT * FROM users WHERE username=?", (username,), one=True)
            if not user or not verify_password(password, user["password_hash"], user["salt"]):
                return self.fail("账号或密码不正确", 401, 401)
            if user.get("status") != "active":
                return self.fail("该账号已被禁用，请联系管理员", 403, 403)
            token = create_session(user["id"])
            execute("UPDATE users SET last_login=? WHERE id=?", (fmt(now()), user["id"]))
            user["last_login"] = fmt(now())
            log_action(user, "登录系统", "认证", "登录成功")
            return self.json_out({"token": token, "user": public_user(user)})

        user = self.require_auth()

        if action == "logout" and method == "POST":
            auth = self.headers.get("Authorization", "")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if token:
                execute("DELETE FROM sessions WHERE token=?", (token,))
            log_action(user, "退出登录", "认证", "")
            return self.json_out(True)

        if action == "me" and method == "GET":
            return self.json_out(public_user(user))

        if action == "profile" and method in ("PUT", "POST"):
            d = self.read_json()
            display_name = (d.get("display_name") or "").strip() or user["display_name"]
            execute("UPDATE users SET display_name=?, email=?, phone=?, avatar=? WHERE id=?",
                    (display_name, d.get("email", ""), d.get("phone", ""),
                     d.get("avatar", ""), user["id"]))
            log_action(user, "更新资料", "账户", display_name)
            return self.json_out(public_user(query("SELECT * FROM users WHERE id=?",
                                                   (user["id"],), one=True)))

        if action == "password" and method in ("PUT", "POST"):
            d = self.read_json()
            row = query("SELECT * FROM users WHERE id=?", (user["id"],), one=True)
            if not verify_password(d.get("old_password") or "", row["password_hash"], row["salt"]):
                raise ApiError("原密码不正确")
            new_pwd = d.get("new_password") or ""
            if len(new_pwd) < 6:
                raise ApiError("新密码至少 6 位")
            h, s = hash_password(new_pwd)
            execute("UPDATE users SET password_hash=?, salt=? WHERE id=?", (h, s, user["id"]))
            log_action(user, "修改密码", "账户", "")
            return self.json_out(True)

        return self.fail("接口不存在", 404)

    # ---------------- 数据看板 ----------------
    def api_dashboard(self, method, rest, qs):
        self.require_auth()

        total = query("SELECT COUNT(*) c FROM contents", one=True)["c"]
        published = query("SELECT COUNT(*) c FROM contents WHERE status='published'", one=True)["c"]
        draft = query("SELECT COUNT(*) c FROM contents WHERE status='draft'", one=True)["c"]
        archived = query("SELECT COUNT(*) c FROM contents WHERE status='archived'", one=True)["c"]
        views = query("SELECT COALESCE(SUM(views),0) v FROM contents", one=True)["v"]
        likes = query("SELECT COALESCE(SUM(likes),0) v FROM contents", one=True)["v"]
        media = query("SELECT COUNT(*) c, COALESCE(SUM(size),0) s FROM attachments", one=True)

        # 近 14 天创建 / 发布趋势
        today = now().date()
        days = [today - dt.timedelta(days=i) for i in range(13, -1, -1)]
        created_rows = query(
            "SELECT substr(created_at,1,10) d, COUNT(*) c FROM contents GROUP BY d")
        pub_rows = query(
            "SELECT substr(published_at,1,10) d, COUNT(*) c FROM contents"
            " WHERE published_at<>'' GROUP BY d")
        view_rows = query(
            "SELECT substr(created_at,1,10) d, COALESCE(SUM(views),0) v FROM contents GROUP BY d")
        cmap = {r["d"]: r["c"] for r in created_rows}
        pmap = {r["d"]: r["c"] for r in pub_rows}
        vmap = {r["d"]: r["v"] for r in view_rows}

        trend = [{
            "date": d.strftime("%m-%d"),
            "created": cmap.get(d.strftime("%Y-%m-%d"), 0),
            "published": pmap.get(d.strftime("%Y-%m-%d"), 0),
            "views": vmap.get(d.strftime("%Y-%m-%d"), 0),
        } for d in days]

        cat_dist = query(
            "SELECT c.name AS name, c.color AS color, COUNT(x.id) AS value"
            " FROM categories c LEFT JOIN contents x ON x.category_id=c.id"
            " GROUP BY c.id ORDER BY c.sort_order")

        status_dist = [{"name": "已发布", "value": published, "color": "#10b981"},
                       {"name": "草稿", "value": draft, "color": "#f59e0b"},
                       {"name": "已归档", "value": archived, "color": "#94a3b8"}]

        top = query(
            "SELECT id,title,views,category_id FROM contents WHERE status='published'"
            " ORDER BY views DESC LIMIT 6")
        recent = query(
            "SELECT id,title,status,author_name,updated_at,views FROM contents"
            " ORDER BY updated_at DESC LIMIT 6")
        logs = query("SELECT * FROM logs ORDER BY id DESC LIMIT 8")
        users_count = query("SELECT COUNT(*) c FROM users", one=True)["c"]

        # 环比：近7天 vs 前7天
        d7 = (today - dt.timedelta(days=6)).strftime("%Y-%m-%d")
        d14 = (today - dt.timedelta(days=13)).strftime("%Y-%m-%d")
        cur = query("SELECT COUNT(*) c FROM contents WHERE substr(created_at,1,10)>=?",
                    (d7,), one=True)["c"]
        prev = query("SELECT COUNT(*) c FROM contents WHERE substr(created_at,1,10)>=?"
                     " AND substr(created_at,1,10)<?", (d14, d7), one=True)["c"]
        growth = round((cur - prev) / prev * 100, 1) if prev else (100.0 if cur else 0.0)

        return self.json_out({
            "kpis": {
                "total": total, "published": published, "draft": draft, "archived": archived,
                "views": views, "likes": likes, "media": media["c"],
                "mediaSize": media["s"], "users": users_count, "growth": growth,
            },
            "trend": trend, "categoryDist": cat_dist, "statusDist": status_dist,
            "topContents": top, "recentContents": recent, "recentLogs": logs,
        })

    # ---------------- 内容管理 ----------------
    def api_contents(self, method, rest, qs):
        user = self.require_auth()

        if rest and rest[0] == "batch" and method == "POST":
            d = self.read_json()
            ids = [int(i) for i in (d.get("ids") or [])]
            if not ids:
                raise ApiError("请选择要操作的内容")
            marks = ",".join("?" * len(ids))
            action = d.get("action") or "delete"
            if action == "delete":
                execute("DELETE FROM contents WHERE id IN (%s)" % marks, ids)
                log_action(user, "批量删除内容", "内容", "共 %d 条" % len(ids))
            elif action in ("published", "draft", "archived"):
                execute("UPDATE contents SET status=?, updated_at=? WHERE id IN (%s)" % marks,
                        [action, fmt(now())] + ids)
                log_action(user, "批量修改状态", "内容", "共 %d 条 → %s" % (len(ids), action))
            else:
                raise ApiError("不支持的操作")
            return self.json_out({"affected": len(ids)})

        if method == "GET" and not rest:
            return self.list_contents(qs)

        if method == "POST" and not rest:
            d = self.read_json()
            title = (d.get("title") or "").strip()
            if not title:
                raise ApiError("标题不能为空")
            status = d.get("status") or "draft"
            t = now()
            pub = fmt(t) if status == "published" else ""
            cid, _ = execute(
                "INSERT INTO contents(title,slug,category_id,tags,summary,body,cover,status,"
                "author_id,author_name,views,likes,is_top,published_at,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (title, d.get("slug") or slugify(title), d.get("category_id"),
                 d.get("tags", ""), d.get("summary", ""), d.get("body", ""),
                 d.get("cover", ""), status, user["id"], user["display_name"],
                 int(d.get("views") or 0), 0, 1 if d.get("is_top") else 0,
                 pub, fmt(t), fmt(t)))
            log_action(user, "创建内容", "内容", title)
            return self.json_out({"id": cid})

        if rest:
            try:
                cid = int(rest[0])
            except Exception:
                raise ApiError("参数错误")
            if method == "GET":
                row = query("SELECT * FROM contents WHERE id=?", (cid,), one=True)
                if not row:
                    raise ApiError("内容不存在", 404)
                return self.json_out(row)
            if method in ("PUT", "POST"):
                d = self.read_json()
                row = query("SELECT * FROM contents WHERE id=?", (cid,), one=True)
                if not row:
                    raise ApiError("内容不存在", 404)
                title = (d.get("title") or "").strip()
                if not title:
                    raise ApiError("标题不能为空")
                status = d.get("status") or row["status"]
                pub = row["published_at"]
                if status == "published" and not pub:
                    pub = fmt(now())
                if status != "published":
                    pub = ""
                execute(
                    "UPDATE contents SET title=?,slug=?,category_id=?,tags=?,summary=?,body=?,"
                    "cover=?,status=?,views=?,is_top=?,published_at=?,updated_at=? WHERE id=?",
                    (title, d.get("slug") or row["slug"], d.get("category_id"),
                     d.get("tags", ""), d.get("summary", ""), d.get("body", ""),
                     d.get("cover", ""), status, int(d.get("views") or row["views"]),
                     1 if d.get("is_top") else 0, pub, fmt(now()), cid))
                log_action(user, "更新内容", "内容", title)
                return self.json_out({"id": cid})
            if method == "DELETE":
                row = query("SELECT title FROM contents WHERE id=?", (cid,), one=True)
                if not row:
                    raise ApiError("内容不存在", 404)
                execute("DELETE FROM contents WHERE id=?", (cid,))
                log_action(user, "删除内容", "内容", row["title"])
                return self.json_out(True)

        return self.fail("接口不存在", 404)

    def list_contents(self, qs):
        page, size = page_params(qs)
        keyword = one(qs, "keyword").strip()
        category = one(qs, "category_id").strip()
        status = one(qs, "status").strip()
        sort = one(qs, "sort", "updated_at")
        order = "ASC" if one(qs, "order", "desc").lower() == "asc" else "DESC"
        if sort not in ("updated_at", "created_at", "views", "likes", "id", "published_at"):
            sort = "updated_at"

        where, args = [], []
        if keyword:
            where.append("(c.title LIKE ? OR c.summary LIKE ? OR c.tags LIKE ?)")
            args += ["%%%s%%" % keyword] * 3
        if category:
            where.append("c.category_id = ?")
            args.append(category)
        if status:
            where.append("c.status = ?")
            args.append(status)
        clause = (" WHERE " + " AND ".join(where)) if where else ""

        total = query("SELECT COUNT(*) c FROM contents c" + clause, args, one=True)["c"]
        rows = query(
            "SELECT c.*, cat.name AS category_name, cat.color AS category_color"
            " FROM contents c LEFT JOIN categories cat ON cat.id=c.category_id"
            + clause + " ORDER BY c.%s %s LIMIT ? OFFSET ?" % (sort, order),
            args + [size, (page - 1) * size])

        summary = {
            "total": query("SELECT COUNT(*) c FROM contents", one=True)["c"],
            "published": query("SELECT COUNT(*) c FROM contents WHERE status='published'",
                               one=True)["c"],
            "draft": query("SELECT COUNT(*) c FROM contents WHERE status='draft'", one=True)["c"],
            "archived": query("SELECT COUNT(*) c FROM contents WHERE status='archived'",
                              one=True)["c"],
        }
        return self.json_out({"list": rows, "total": total, "page": page,
                              "size": size, "pages": max(1, (total + size - 1) // size),
                              "summary": summary})

    # ---------------- 分类 ----------------
    def api_categories(self, method, rest, qs):
        user = self.require_auth()
        if method == "GET":
            rows = query(
                "SELECT c.*, (SELECT COUNT(*) FROM contents x WHERE x.category_id=c.id) AS count"
                " FROM categories c ORDER BY c.sort_order, c.id")
            return self.json_out(rows)

        if method == "POST" and not rest:
            d = self.read_json()
            name = (d.get("name") or "").strip()
            if not name:
                raise ApiError("分类名称不能为空")
            if query("SELECT id FROM categories WHERE name=?", (name,), one=True):
                raise ApiError("分类名称已存在")
            cid, _ = execute(
                "INSERT INTO categories(name,slug,color,sort_order,created_at) VALUES(?,?,?,?,?)",
                (name, slugify(name), d.get("color") or "#4f6ef7",
                 int(d.get("sort_order") or 0), fmt(now())))
            log_action(user, "创建分类", "分类", name)
            return self.json_out({"id": cid})

        if rest:
            cid = int(rest[0])
            if method in ("PUT", "POST"):
                d = self.read_json()
                name = (d.get("name") or "").strip()
                if not name:
                    raise ApiError("分类名称不能为空")
                execute("UPDATE categories SET name=?, color=?, sort_order=? WHERE id=?",
                        (name, d.get("color") or "#4f6ef7",
                         int(d.get("sort_order") or 0), cid))
                log_action(user, "更新分类", "分类", name)
                return self.json_out(True)
            if method == "DELETE":
                row = query("SELECT name FROM categories WHERE id=?", (cid,), one=True)
                if not row:
                    raise ApiError("分类不存在", 404)
                execute("UPDATE contents SET category_id=NULL WHERE category_id=?", (cid,))
                execute("DELETE FROM categories WHERE id=?", (cid,))
                log_action(user, "删除分类", "分类", row["name"])
                return self.json_out(True)

        return self.fail("接口不存在", 404)

    # ---------------- 媒体库 ----------------
    def api_media(self, method, rest, qs):
        user = self.require_auth()
        if method == "GET":
            page, size = page_params(qs)
            keyword = one(qs, "keyword").strip()
            where, args = "", []
            if keyword:
                where = " WHERE filename LIKE ?"
                args = ["%%%s%%" % keyword]
            total = query("SELECT COUNT(*) c FROM attachments" + where, args, one=True)["c"]
            rows = query("SELECT * FROM attachments" + where +
                         " ORDER BY id DESC LIMIT ? OFFSET ?", args + [size, (page - 1) * size])
            stat = query("SELECT COUNT(*) c, COALESCE(SUM(size),0) s FROM attachments", one=True)
            return self.json_out({"list": rows, "total": total, "page": page, "size": size,
                                  "pages": max(1, (total + size - 1) // size),
                                  "count": stat["c"], "size": stat["s"]})
        if method == "DELETE" and rest:
            row = query("SELECT * FROM attachments WHERE id=?", (int(rest[0]),), one=True)
            if not row:
                raise ApiError("文件不存在", 404)
            path = os.path.join(UPLOAD_DIR, row["stored_name"])
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            execute("DELETE FROM attachments WHERE id=?", (row["id"],))
            log_action(user, "删除文件", "媒体", row["filename"])
            return self.json_out(True)
        return self.fail("接口不存在", 404)

    def api_upload(self, method, rest, qs):
        user = self.require_auth()
        if method != "POST":
            return self.fail("接口不存在", 404)
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r"boundary=([^;]+)", ctype)
        if "multipart/form-data" not in ctype or not m:
            raise ApiError("请以 multipart/form-data 方式上传文件")
        fields, files = parse_multipart(self.read_body(), m.group(1).strip('"').encode())
        if not files:
            raise ApiError("未检测到上传文件")
        f = list(files.values())[0]
        ext = os.path.splitext(f["filename"])[1].lower()
        if ext not in SAFE_EXT:
            raise ApiError("不支持的文件类型：%s" % (ext or "未知"))
        stored = "%s%s" % (dt.datetime.now().strftime("%Y%m%d%H%M%S"), uuid.uuid4().hex[:8])
        stored += ext
        with open(os.path.join(UPLOAD_DIR, stored), "wb") as fh:
            fh.write(f["content"])
        mime = f["mime"] or mimetypes.guess_type(f["filename"])[0] or "application/octet-stream"
        url = "/uploads/" + stored
        fid, _ = execute(
            "INSERT INTO attachments(filename,stored_name,mime,size,url,uploader,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (f["filename"], stored, mime, len(f["content"]), url,
             user["display_name"], fmt(now())))
        log_action(user, "上传文件", "媒体", f["filename"])
        return self.json_out({"id": fid, "url": url, "filename": f["filename"],
                              "size": len(f["content"]), "mime": mime})

    # ---------------- 用户管理 ----------------
    def api_users(self, method, rest, qs):
        user = self.require_auth()
        if method == "GET":
            rows = query("SELECT * FROM users ORDER BY id")
            return self.json_out([public_user(r) for r in rows])

        if method == "POST" and not rest:
            d = self.read_json()
            username = (d.get("username") or "").strip()
            password = d.get("password") or ""
            if not username or len(password) < 6:
                raise ApiError("账号必填，密码至少 6 位")
            if query("SELECT id FROM users WHERE username=?", (username,), one=True):
                raise ApiError("账号已存在")
            h, s = hash_password(password)
            uid, _ = execute(
                "INSERT INTO users(username,display_name,password_hash,salt,role,email,phone,"
                "status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (username, (d.get("display_name") or username), h, s,
                 d.get("role") or "editor", d.get("email", ""), d.get("phone", ""),
                 d.get("status") or "active", fmt(now())))
            log_action(user, "创建用户", "用户", username)
            return self.json_out({"id": uid})

        if rest:
            uid = int(rest[0])
            if method in ("PUT", "POST"):
                d = self.read_json()
                execute("UPDATE users SET display_name=?,role=?,email=?,phone=?,status=? WHERE id=?",
                        (d.get("display_name") or "", d.get("role") or "editor",
                         d.get("email", ""), d.get("phone", ""),
                         d.get("status") or "active", uid))
                if d.get("password"):
                    h, s = hash_password(d["password"])
                    execute("UPDATE users SET password_hash=?,salt=? WHERE id=?", (h, s, uid))
                log_action(user, "更新用户", "用户", str(uid))
                return self.json_out(True)
            if method == "DELETE":
                if uid == user["id"]:
                    raise ApiError("不能删除当前登录账号")
                if query("SELECT COUNT(*) c FROM users", one=True)["c"] <= 1:
                    raise ApiError("至少保留一个账号")
                execute("DELETE FROM users WHERE id=?", (uid,))
                execute("DELETE FROM sessions WHERE user_id=?", (uid,))
                log_action(user, "删除用户", "用户", str(uid))
                return self.json_out(True)
        return self.fail("接口不存在", 404)

    # ---------------- 操作日志 ----------------
    def api_logs(self, method, rest, qs):
        self.require_auth()
        page, size = page_params(qs)
        keyword = one(qs, "keyword").strip()
        where, args = "", []
        if keyword:
            where = " WHERE username LIKE ? OR action LIKE ? OR target LIKE ?"
            args = ["%%%s%%" % keyword] * 3
        total = query("SELECT COUNT(*) c FROM logs" + where, args, one=True)["c"]
        rows = query("SELECT * FROM logs" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
                     args + [size, (page - 1) * size])
        return self.json_out({"list": rows, "total": total, "page": page, "size": size,
                              "pages": max(1, (total + size - 1) // size)})

    # ---------------- 公共方法 ----------------
    def require_auth(self):
        user = resolve_token(self)
        if not user:
            raise ApiError("登录状态已失效，请重新登录", 401)
        return user


# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------
def main():
    init_db()
    class QuietServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

        def handle_error(self, request, client_address):
            exc = sys.exc_info()[1]
            if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError, ConnectionAbortedError)):
                return  # 客户端提前断开属正常现象，不打印堆栈
            import traceback
            traceback.print_exc()

    httpd = QuietServer((HOST, PORT), Handler)
    print("内容管理后台已启动： http://%s:%d" % (HOST, PORT), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
