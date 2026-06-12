#!/usr/bin/env python3
"""Khazix Skills 统一 Web 服务器。

仅使用 Python 标准库，提供静态文件服务、API 端点和代理功能。

端点：
  GET  /                       → web/index.html
  GET  /api/health             → 健康检查
  GET  /api/skills             → 返回 8 个 Skill 的元数据 JSON
  GET  /api/skills/{skill_id}  → 返回单个 Skill 详情（含 SKILL.md 内容）
  GET  /api/docs               → API 文档页面（HTML）
  GET  /api/aihot/daily        → 代理 aihot 日报 API
  GET  /api/aihot/items        → 代理 aihot 条目 API（默认 mode=selected&take=20）
  GET  /api/aihot/dailies      → 代理 aihot 日报归档列表
  GET  /api/aihot/search?q=kw  → 代理 aihot 搜索
  POST /api/scan               → 执行存储扫描，返回扫描结果 JSON
  GET  /api/report             → 返回存储分析报告 HTML 页面
  POST /api/webapp-test        → Web 应用测试
  POST /api/security-scan      → 安全扫描
  POST /api/changelog          → 变更日志生成
  静态文件                      → web/ 目录
"""
import ipaddress
import json
import mimetypes
import os
import random
import socket
import string
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
STORAGE_ANALYZER_DIR = BASE_DIR / "storage-analyzer"
SCAN_SCRIPT_DIR = STORAGE_ANALYZER_DIR / "scripts"
REPORT_TEMPLATE = STORAGE_ANALYZER_DIR / "assets" / "report_template.html"

AIHOT_BASE = "https://aihot.virxact.com"
AIHOT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

PORT = 8000
MAX_URL_LENGTH = 2048
START_TIME = time.time()

# ---------------------------------------------------------------------------
# SSRF 防护：私有/保留 IP 网段
# ---------------------------------------------------------------------------
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
]

_METADATA_IP = ipaddress.ip_address("169.254.169.254")

# ---------------------------------------------------------------------------
# 限流器
# ---------------------------------------------------------------------------
class RateLimiter:
    """简单的内存限流器，按 IP 跟踪请求频率。"""

    def __init__(self):
        self._lock = threading.Lock()
        # key: (ip, bucket) → {"timestamps": [...]}
        self._buckets: dict = {}

    def is_allowed(self, ip: str, bucket: str, limit: int, window: int = 60) -> bool:
        """检查请求是否被允许。

        Args:
            ip: 客户端 IP
            bucket: 限流桶名称（如 "api" / "expensive"）
            limit: 窗口期内允许的最大请求数
            window: 窗口期秒数（默认 60）

        Returns:
            True 表示允许，False 表示超限
        """
        now = time.time()
        key = (ip, bucket)
        with self._lock:
            if key not in self._buckets:
                self._buckets[key] = {"timestamps": []}
            entry = self._buckets[key]
            # 清理过期时间戳
            entry["timestamps"] = [t for t in entry["timestamps"] if now - t < window]
            if len(entry["timestamps"]) >= limit:
                return False
            entry["timestamps"].append(now)
            return True

_rate_limiter = RateLimiter()

# 限流配置：桶名 → (限制数, 窗口秒数)
RATE_LIMITS = {
    "api": (30, 60),        # 普通 API：30 次/分钟
    "expensive": (5, 60),   # 昂贵操作（scan/test/scan）：5 次/分钟
}

# ---------------------------------------------------------------------------
# Skills 元数据
# ---------------------------------------------------------------------------
SKILLS = [
    {
        "id": "storage-analyzer",
        "name": "存储分析",
        "english_name": "Storage Analyzer",
        "icon": "💾",
        "description": "macOS / Windows 只读存储分析助手。扫描整机磁盘占用，找出占空间大户，把每一项分成 🟢可自动清理 / 🟡需人工判断 / 🔴谨慎清理 三级",
        "one_liner": "一句话扫描整机磁盘，三色分级给清理决策，网页上一键移废纸篓",
        "tags": ["macOS", "Windows", "实用工具", "磁盘清理"],
        "trigger_words": ["存储分析", "磁盘满了", "C盘满了", "清理空间", "storage analysis"],
        "platform": ["macOS", "Windows"],
        "agent_compatibility": ["Claude Code", "Codex", "OpenCode", "OpenClaw"],
        "skill_md_path": "storage-analyzer/SKILL.md",
    },
    {
        "id": "aihot",
        "name": "AI HOT",
        "english_name": "AI HOT",
        "icon": "🔥",
        "description": "AI HOT (aihot.virxact.com) 中文 AI 资讯查询 Skill。让 Agent 用一句话拿到每天的 AI 日报和全部 AI 动态，无需 API Key",
        "one_liner": "一句话拿到每天的 AI 日报和全部 AI 动态，无需 API Key",
        "tags": ["资讯", "AI新闻", "日报"],
        "trigger_words": ["AI资讯", "AI新闻", "AI日报", "aihot", "AI动态"],
        "platform": ["macOS", "Windows", "Linux"],
        "agent_compatibility": ["Claude Code", "Codex", "OpenCode", "OpenClaw"],
        "skill_md_path": "aihot/SKILL.md",
    },
    {
        "id": "neat-freak",
        "name": "洁癖",
        "english_name": "Neat Freak",
        "icon": "✨",
        "description": "End-of-session knowledge cleanup with OCD-level rigor — reconciles project docs and agent memory against the code so nothing rots",
        "one_liner": "会话结束后对项目文档和记忆进行洁癖级审查与同步，确保没有过时内容",
        "tags": ["文档", "同步", "记忆", "清理"],
        "trigger_words": ["洁癖", "文档同步", "记忆清理", "neat freak", "cleanup"],
        "platform": ["macOS", "Windows", "Linux"],
        "agent_compatibility": ["Claude Code", "Codex", "OpenCode", "OpenClaw"],
        "skill_md_path": "neat-freak/SKILL.md",
    },
    {
        "id": "hv-analysis",
        "name": "横纵分析法",
        "english_name": "HV Analysis",
        "icon": "🔬",
        "description": "横纵分析法深度研究 Skill。纵向追踪发展历程，横向对比竞品，交汇产出洞察，最终生成万字 PDF 研究报告",
        "one_liner": "纵向追踪发展历程，横向对比竞品，交汇产出洞察，生成万字 PDF 研究报告",
        "tags": ["研究", "分析", "竞品", "PDF"],
        "trigger_words": ["横纵分析", "深度研究", "竞品分析", "研究报告", "hv analysis"],
        "platform": ["macOS", "Windows", "Linux"],
        "agent_compatibility": ["Claude Code", "Codex", "OpenCode", "OpenClaw"],
        "skill_md_path": "hv-analysis/SKILL.md",
    },
    {
        "id": "khazix-writer",
        "name": "卡兹克写作",
        "english_name": "Khazix Writer",
        "icon": "✍️",
        "description": "数字生命卡兹克的公众号长文写作 Skill。装上之后，Agent 用卡兹克的口吻和节奏写公众号长文",
        "one_liner": "用数字生命卡兹克的口吻和节奏写公众号长文，活人感文风",
        "tags": ["写作", "公众号", "长文", "内容创作"],
        "trigger_words": ["卡兹克", "公众号写作", "长文写作", "khazix writer", "写文章"],
        "platform": ["macOS", "Windows", "Linux"],
        "agent_compatibility": ["Claude Code", "Codex", "OpenCode", "OpenClaw"],
        "skill_md_path": "khazix-writer/SKILL.md",
    },
    {
        "id": "webapp-testing",
        "name": "Web 应用测试",
        "english_name": "WebApp Testing",
        "icon": "🧪",
        "description": "Web 应用自动化测试 Skill。让 Agent 用一句话启动 Web 应用测试，自动检测页面可访问性、链接完整性、性能指标、安全头配置，生成 HTML 测试报告",
        "one_liner": "一句话启动 Web 应用全面体检，自动检测可访问性/链接/性能/安全，生成 HTML 测试报告",
        "tags": ["测试", "Web", "性能", "安全", "自动化"],
        "trigger_words": ["测试网站", "web测试", "页面检测", "网站体检", "webapp test"],
        "platform": ["macOS", "Windows", "Linux"],
        "agent_compatibility": ["Claude Code", "Codex", "OpenCode", "Cursor"],
        "skill_md_path": "webapp-testing/SKILL.md",
    },
    {
        "id": "changelog-generator",
        "name": "变更日志生成器",
        "english_name": "Changelog Generator",
        "icon": "📋",
        "description": "Git 变更日志自动生成 Skill。让 Agent 从 git 历史自动生成用户友好的 Changelog，支持 Conventional Commits 解析、版本分类、Breaking Changes 高亮",
        "one_liner": "从 git 历史自动生成用户友好的 Changelog，支持 Conventional Commits 和版本分类",
        "tags": ["Git", "变更日志", "版本管理", "自动化"],
        "trigger_words": ["生成变更日志", "changelog", "版本更新记录", "release notes"],
        "platform": ["macOS", "Windows", "Linux"],
        "agent_compatibility": ["Claude Code", "Codex", "OpenCode", "Cursor"],
        "skill_md_path": "changelog-generator/SKILL.md",
    },
    {
        "id": "security-scanner",
        "name": "安全扫描器",
        "english_name": "Security Scanner",
        "icon": "🛡️",
        "description": "Web 应用安全扫描 Skill。让 Agent 对目标 Web 应用进行基础安全扫描，检测常见漏洞（信息泄露、不安全头、混合内容、过期 SSL），生成安全评估报告",
        "one_liner": "一句话对 Web 应用做安全体检，检测信息泄露/不安全头/混合内容/过期 SSL，生成评估报告",
        "tags": ["安全", "扫描", "漏洞检测", "Web安全"],
        "trigger_words": ["安全扫描", "安全检测", "漏洞扫描", "security scan"],
        "platform": ["macOS", "Windows", "Linux"],
        "agent_compatibility": ["Claude Code", "Codex", "OpenCode", "Cursor"],
        "skill_md_path": "security-scanner/SKILL.md",
    },
]

# 构建 id → skill 的快速查找表
_SKILL_MAP = {s["id"]: s for s in SKILLS}

# ---------------------------------------------------------------------------
# 缓存：最近一次扫描结果
# ---------------------------------------------------------------------------
_scan_cache = {"data": None, "timestamp": 0}

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _generate_request_id(length: int = 8) -> str:
    """生成短随机请求 ID。"""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _validate_url(url: str) -> tuple:
    """验证 URL 是否安全（SSRF 防护）。

    检查项：
      - 仅允许 http/https 协议
      - 阻止私有/内部 IP 地址
      - 阻止链路本地地址
      - 阻止云元数据端点
      - 通过 DNS 解析后再次检查 IP

    Returns:
        (is_valid, error_message) 元组
    """
    if len(url) > MAX_URL_LENGTH:
        return False, f"URL 长度超过限制（最大 {MAX_URL_LENGTH} 字符）"

    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()

    if scheme not in ("http", "https"):
        return False, f"不允许的协议: {scheme}，仅支持 http/https"

    hostname = parsed.hostname
    if not hostname:
        return False, "URL 缺少主机名"

    # 阻止 localhost 等特殊主机名
    if hostname.lower() in ("localhost", "localhost.localdomain"):
        return False, "不允许访问 localhost"

    # 通过 DNS 解析后检查 IP
    try:
        addr_infos = socket.getaddrinfo(hostname, parsed.port or 80,
                                        proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False, f"无法解析主机名: {hostname}"

    for family, _type, _proto, _canon, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        # 检查元数据端点
        if ip == _METADATA_IP:
            return False, "不允许访问云元数据端点"

        # 检查私有/保留网段
        for network in _PRIVATE_NETWORKS:
            if ip in network:
                return False, f"不允许访问内部/私有地址: {ip_str}"

    return True, ""


def _validate_repo_path(repo_path: str) -> tuple:
    """验证 repo_path 是否为安全的本地路径。

    Returns:
        (is_valid, resolved_path_or_error_message) 元组
    """
    if not repo_path:
        return True, ""

    p = Path(repo_path).resolve()

    # 路径必须存在
    if not p.exists():
        return False, f"路径不存在: {repo_path}"

    # 路径必须在 BASE_DIR 内或为当前工作目录的子目录
    try:
        p.relative_to(BASE_DIR)
    except ValueError:
        # 不在 BASE_DIR 内，检查是否为绝对路径且存在
        if not p.is_absolute():
            return False, "仅允许绝对路径或项目内相对路径"

    return True, str(p)


def _read_skill_md(skill_id: str) -> str:
    """读取指定 Skill 的 SKILL.md 内容。"""
    skill = _SKILL_MAP.get(skill_id)
    if not skill:
        return ""
    md_path = BASE_DIR / skill["skill_md_path"]
    try:
        return md_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _proxy_aihot(path: str, query: str = "") -> tuple:
    """代理 aihot API 请求，返回 (status_code, body_bytes, content_type)。"""
    url = AIHOT_BASE + path
    if query:
        url += "?" + query
    req = urllib.request.Request(url, headers={"User-Agent": AIHOT_UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            ct = resp.headers.get("Content-Type", "application/json")
            return resp.status, body, ct
    except urllib.error.HTTPError as e:
        return e.code, e.read(), "application/json"
    except Exception as e:
        return 502, json.dumps({"error": str(e)}).encode("utf-8"), "application/json"


def _run_scan() -> dict:
    """执行存储扫描，返回扫描结果字典。"""
    scan_dir = str(SCAN_SCRIPT_DIR)
    if scan_dir not in sys.path:
        sys.path.insert(0, scan_dir)

    import scan as scan_mod

    started = time.time()
    if sys.platform == "darwin":
        system, groups = scan_mod.scan_macos()
    elif sys.platform.startswith("win"):
        system, groups = scan_mod.scan_windows()
    else:
        return {
            "error": "unsupported_platform",
            "platform": sys.platform,
            "message": "scan.py supports macOS and Windows only.",
        }

    data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "system": system,
        "groups": groups,
        "scan_seconds": round(time.time() - started, 1),
    }
    return data


def _log_request(method: str, path: str, status: int = 0, request_id: str = ""):
    """打印请求日志到 stdout。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rid = f" [{request_id}]" if request_id else ""
    suffix = f" → {status}" if status else ""
    print(f"[{ts}]{rid} {method} {path}{suffix}", flush=True)

# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class SkillHandler(BaseHTTPRequestHandler):
    """Khazix Skills 统一请求处理器。"""

    # 静默默认日志，我们用自己的格式
    def log_message(self, format, *args):
        pass

    # ---- 请求 ID 与客户端 IP ----

    def _get_request_id(self) -> str:
        """获取或生成当前请求的 ID。"""
        if not hasattr(self, "_request_id"):
            self._request_id = _generate_request_id()
        return self._request_id

    def _get_client_ip(self) -> str:
        """获取客户端 IP（支持代理头）。"""
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0]

    # ---- 限流 ----

    def _check_rate_limit(self, bucket: str) -> bool:
        """检查限流，返回 True 表示通过。"""
        ip = self._get_client_ip()
        limit, window = RATE_LIMITS.get(bucket, (30, 60))
        return _rate_limiter.is_allowed(ip, bucket, limit, window)

    # ---- 通用响应辅助 ----

    def _security_headers(self):
        """添加安全响应头。"""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-XSS-Protection", "1; mode=block")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self._security_headers()
        self.send_header("X-Request-Id", self._get_request_id())
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, code, data, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._cors_headers()
        self._security_headers()
        self.send_header("X-Request-Id", self._get_request_id())
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, code, message):
        rid = self._get_request_id()
        self._send_json(code, {"error": message, "request_id": rid})

    # ---- OPTIONS (CORS preflight) ----

    def do_OPTIONS(self):
        _log_request("OPTIONS", self.path, request_id=self._get_request_id())
        self.send_response(204)
        self._cors_headers()
        self._security_headers()
        self.end_headers()

    # ---- GET ----

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parsed.query
        rid = self._get_request_id()

        # 限流检查（普通 API）
        if path.startswith("/api/") and not self._check_rate_limit("api"):
            _log_request("GET", self.path, 429, request_id=rid)
            self._send_error(429, "请求过于频繁，请稍后再试")
            return

        # 首页
        if path == "" or path == "/index.html":
            _log_request("GET", self.path, request_id=rid)
            self._serve_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return

        # API: 健康检查
        if path == "/api/health":
            _log_request("GET", self.path, request_id=rid)
            self._send_json(200, {
                "status": "ok",
                "version": "2.0.0",
                "skills_count": len(SKILLS),
                "uptime_seconds": round(time.time() - START_TIME, 1),
            })
            return

        # API: Skills 列表
        if path == "/api/skills":
            _log_request("GET", self.path, request_id=rid)
            self._send_json(200, SKILLS)
            return

        # API: 单个 Skill 详情
        if path.startswith("/api/skills/"):
            skill_id = path[len("/api/skills/"):]
            _log_request("GET", self.path, request_id=rid)
            self._handle_skill_detail(skill_id)
            return

        # API: 文档页面
        if path == "/api/docs":
            _log_request("GET", self.path, request_id=rid)
            html = _generate_api_docs_html()
            self._send_bytes(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return

        # API: aihot 日报代理
        if path == "/api/aihot/daily":
            _log_request("GET", self.path, request_id=rid)
            status, body, ct = _proxy_aihot("/api/public/daily", query)
            self._send_bytes(status, body, ct)
            return

        # API: aihot 条目代理
        if path == "/api/aihot/items":
            _log_request("GET", self.path, request_id=rid)
            params = urllib.parse.parse_qs(query)
            parts = []
            if "mode" not in params:
                parts.append("mode=selected")
            if "take" not in params:
                parts.append("take=20")
            if parts:
                query += ("&" if query else "") + "&".join(parts)
            status, body, ct = _proxy_aihot("/api/public/items", query)
            self._send_bytes(status, body, ct)
            return

        # API: aihot 日报归档列表
        if path == "/api/aihot/dailies":
            _log_request("GET", self.path, request_id=rid)
            status, body, ct = _proxy_aihot("/api/public/dailies", query)
            self._send_bytes(status, body, ct)
            return

        # API: aihot 搜索
        if path == "/api/aihot/search":
            _log_request("GET", self.path, request_id=rid)
            params = urllib.parse.parse_qs(query)
            q = params.get("q", [""])[0]
            if not q:
                self._send_error(400, "Missing query parameter: q")
                return
            search_query = urllib.parse.urlencode({"q": q, "mode": "search"})
            status, body, ct = _proxy_aihot("/api/public/items", search_query)
            self._send_bytes(status, body, ct)
            return

        # API: 存储报告页面
        if path == "/api/report":
            _log_request("GET", self.path, request_id=rid)
            self._serve_report()
            return

        # 静态文件（web/ 目录下）
        rel_path = parsed.path.lstrip("/")
        file_path = WEB_DIR / rel_path
        if file_path.is_file() and WEB_DIR in file_path.resolve().parents:
            _log_request("GET", self.path, request_id=rid)
            mime, _ = mimetypes.guess_type(str(file_path))
            mime = mime or "application/octet-stream"
            self._serve_file(file_path, mime)
            return

        _log_request("GET", self.path, 404, request_id=rid)
        self._send_error(404, "Not Found")

    # ---- POST ----

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        rid = self._get_request_id()

        # 限流检查
        expensive_paths = {"/api/scan", "/api/webapp-test", "/api/security-scan"}
        bucket = "expensive" if path in expensive_paths else "api"
        if not self._check_rate_limit(bucket):
            _log_request("POST", self.path, 429, request_id=rid)
            self._send_error(429, "请求过于频繁，请稍后再试")
            return

        # Content-Type 校验（仅对 API 端点）
        if path.startswith("/api/"):
            ct = self.headers.get("Content-Type", "")
            if ct and "application/json" not in ct.lower():
                _log_request("POST", self.path, 400, request_id=rid)
                self._send_error(400, "Content-Type 必须为 application/json")
                return

        # API: 执行存储扫描
        if path == "/api/scan":
            _log_request("POST", self.path, request_id=rid)
            self._handle_scan()
            return

        # API: Web 应用测试
        if path == "/api/webapp-test":
            _log_request("POST", self.path, request_id=rid)
            self._handle_webapp_test()
            return

        # API: 安全扫描
        if path == "/api/security-scan":
            _log_request("POST", self.path, request_id=rid)
            self._handle_security_scan()
            return

        # API: 变更日志生成
        if path == "/api/changelog":
            _log_request("POST", self.path, request_id=rid)
            self._handle_changelog()
            return

        _log_request("POST", self.path, 404, request_id=rid)
        self._send_error(404, "Not Found")

    # ---- 业务方法 ----

    def _serve_file(self, file_path: Path, content_type: str):
        try:
            data = file_path.read_bytes()
            self._send_bytes(200, data, content_type)
        except OSError:
            self._send_error(404, "File Not Found")

    def _handle_skill_detail(self, skill_id: str):
        """返回单个 Skill 的详情，包含 SKILL.md 内容。"""
        skill = _SKILL_MAP.get(skill_id)
        if not skill:
            self._send_error(404, f"Skill not found: {skill_id}")
            return
        detail = dict(skill)
        detail["skill_md_content"] = _read_skill_md(skill_id)
        self._send_json(200, detail)

    def _serve_report(self):
        """提供存储分析报告 HTML 页面。"""
        if _scan_cache["data"] is None:
            self._send_json(200, {
                "message": "尚未执行扫描，请先 POST /api/scan",
                "hint": "扫描完成后刷新此页面即可查看报告",
            })
            return

        try:
            template = REPORT_TEMPLATE.read_text(encoding="utf-8")
        except OSError:
            self._send_error(500, "报告模板文件不存在")
            return

        blob = json.dumps(_scan_cache["data"], ensure_ascii=False)
        html = template.replace("__REPORT_DATA__", blob).replace("__DELETE_CONFIG__", "null")
        self._send_bytes(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _handle_scan(self):
        """执行存储扫描并缓存结果。"""
        try:
            data = _run_scan()
            _scan_cache["data"] = data
            _scan_cache["timestamp"] = time.time()
            self._send_json(200, data)
        except Exception as e:
            self._send_error(500, f"扫描失败: {e}")

    def _read_json_body(self):
        """读取 POST 请求的 JSON body，带异常处理。"""
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        if length > 10 * 1024 * 1024:  # 10MB 上限
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"无效的 JSON: {e}")

    def _handle_webapp_test(self):
        """执行 Web 应用健康检查。"""
        try:
            body = self._read_json_body()
        except ValueError as e:
            self._send_error(400, str(e))
            return

        url = body.get("url", "")
        if not url:
            self._send_error(400, "Missing required parameter: url")
            return

        # SSRF 防护
        is_valid, err_msg = _validate_url(url)
        if not is_valid:
            self._send_error(400, f"URL 验证失败: {err_msg}")
            return

        script = str(BASE_DIR / "webapp-testing" / "scripts" / "health_check.py")
        try:
            result = subprocess.run(
                [sys.executable, script, url],
                capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            self._send_error(504, "Web 应用测试超时（60s）")
            return

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            self._send_error(500, f"脚本输出非有效 JSON: {result.stdout[:500] or result.stderr[:500]}")
            return
        self._send_json(200, data)

    def _handle_security_scan(self):
        """执行安全扫描。"""
        try:
            body = self._read_json_body()
        except ValueError as e:
            self._send_error(400, str(e))
            return

        url = body.get("url", "")
        if not url:
            self._send_error(400, "Missing required parameter: url")
            return

        # SSRF 防护
        is_valid, err_msg = _validate_url(url)
        if not is_valid:
            self._send_error(400, f"URL 验证失败: {err_msg}")
            return

        script = str(BASE_DIR / "security-scanner" / "scripts" / "scan_security.py")
        try:
            result = subprocess.run(
                [sys.executable, script, url],
                capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            self._send_error(504, "安全扫描超时（60s）")
            return

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            self._send_error(500, f"脚本输出非有效 JSON: {result.stdout[:500] or result.stderr[:500]}")
            return
        self._send_json(200, data)

    def _handle_changelog(self):
        """执行变更日志生成。"""
        try:
            body = self._read_json_body()
        except ValueError as e:
            self._send_error(400, str(e))
            return

        repo_path = body.get("repo_path", "")
        from_ref = body.get("from_ref", "")
        to_ref = body.get("to_ref", "")

        # 验证 repo_path
        is_valid, result = _validate_repo_path(repo_path)
        if not is_valid:
            self._send_error(400, f"repo_path 验证失败: {result}")
            return
        resolved_path = result  # 解析后的安全路径

        script = str(BASE_DIR / "changelog-generator" / "scripts" / "gen_changelog.py")
        cmd = [sys.executable, script]
        if resolved_path:
            cmd.append(resolved_path)
        if from_ref:
            cmd.append(f"--from={from_ref}")
        if to_ref:
            cmd.append(f"--to={to_ref}")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
                cwd=str(BASE_DIR),
            )
        except subprocess.TimeoutExpired:
            self._send_error(504, "变更日志生成超时（60s）")
            return

        # changelog 脚本输出 Markdown，包装为 JSON
        output = result.stdout.strip()
        if output:
            data = {"changelog": output, "format": "markdown"}
        else:
            err = result.stderr.strip() or "No output"
            data = {"error": err, "changelog": ""}
        self._send_json(200, data)


# ---------------------------------------------------------------------------
# API 文档页面生成
# ---------------------------------------------------------------------------

def _generate_api_docs_html() -> str:
    """生成 API 文档 HTML 页面。"""
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Khazix Skills — API 文档</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🧰</text></svg>">
<style>
:root{--primary:#165DFF;--primary-dark:#0e42d2;--primary-light:#4080ff;--green:#00b42a;--green-bg:rgba(0,180,42,.1);--orange:#ff7d00;--orange-bg:rgba(255,125,0,.1);--red:#f53f3f;--red-bg:rgba(245,63,63,.1);--purple:#722ed1;--purple-bg:rgba(114,46,209,.1);--blue:#3498db;--blue-bg:rgba(52,152,219,.1);--radius:12px;--radius-sm:8px;--font:system-ui,-apple-system,"PingFang SC","Microsoft YaHei","Noto Sans SC",sans-serif;--mono:"SF Mono","Fira Code",ui-monospace,monospace;--bg:#0d1117;--bg2:#161b22;--card:#1c2333;--text:#e6edf3;--text2:#8b949e;--text3:#484f58;--border:#30363d}
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:var(--font);background:var(--bg);color:var(--text);line-height:1.7}
a{color:var(--primary-light);text-decoration:none}a:hover{color:var(--green)}
::selection{background:var(--primary);color:#fff}
::-webkit-scrollbar{width:6px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
.page{max-width:960px;margin:0 auto;padding:40px 24px 80px}
.page-head{margin-bottom:48px}
.page-head h1{font-size:2rem;font-weight:800;margin-bottom:8px;background:linear-gradient(135deg,var(--text),var(--primary-light));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.page-head p{color:var(--text2);font-size:1rem}
.page-head .back-link{display:inline-flex;align-items:center;gap:6px;margin-bottom:16px;font-size:.88rem;color:var(--text2)}
.page-head .back-link:hover{color:var(--primary-light)}
.cat-section{margin-bottom:40px}
.cat-title{display:flex;align-items:center;gap:10px;font-size:1.2rem;font-weight:700;margin-bottom:16px;padding-bottom:10px;border-bottom:1px solid var(--border)}
.cat-title .cat-icon{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:1rem}
.cat-icon.bg-blue{background:var(--blue-bg);color:var(--blue)}
.cat-icon.bg-green{background:var(--green-bg);color:var(--green)}
.cat-icon.bg-orange{background:var(--orange-bg);color:var(--orange)}
.cat-icon.bg-red{background:var(--red-bg);color:var(--red)}
.cat-icon.bg-purple{background:var(--purple-bg);color:var(--purple)}
.ep-card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:24px;margin-bottom:16px;transition:border-color .2s}
.ep-card:hover{border-color:var(--primary)}
.ep-head{display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap}
.ep-method{padding:4px 12px;border-radius:6px;font-size:.78rem;font-weight:700;font-family:var(--mono);letter-spacing:.04em}
.ep-method.get{background:var(--blue-bg);color:var(--blue)}
.ep-method.post{background:var(--green-bg);color:var(--green)}
.ep-path{font-family:var(--mono);font-size:.92rem;font-weight:600;color:var(--text)}
.ep-desc{color:var(--text2);font-size:.88rem;margin-bottom:14px}
.ep-section{margin-bottom:14px}
.ep-section-title{font-size:.82rem;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
.ep-table{width:100%;border-collapse:collapse;font-size:.84rem}
.ep-table th{text-align:left;padding:8px 12px;background:var(--bg2);color:var(--text3);font-weight:600;border:1px solid var(--border)}
.ep-table td{padding:8px 12px;border:1px solid var(--border);color:var(--text2)}
.ep-table td:first-child{color:var(--primary-light);font-family:var(--mono);font-weight:500}
.ep-table td code{font-family:var(--mono);font-size:.8rem;background:var(--bg2);padding:2px 6px;border-radius:4px}
.ep-code-block{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:14px 18px;font-family:var(--mono);font-size:.82rem;color:var(--text2);overflow-x:auto;line-height:1.6;white-space:pre-wrap;word-break:break-all}
.ep-code-block .hl-method{color:var(--green);font-weight:700}
.ep-code-block .hl-url{color:var(--primary-light)}
.ep-code-block .hl-key{color:var(--orange)}
.ep-code-block .hl-str{color:var(--green)}
.ep-code-block .hl-comment{color:var(--text3);font-style:italic}
.ep-resp-block{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:14px 18px;font-family:var(--mono);font-size:.82rem;color:var(--text2);overflow-x:auto;line-height:1.6;white-space:pre-wrap;word-break:break-all}
.ep-resp-block .hl-key{color:var(--primary-light)}
.ep-resp-block .hl-str{color:var(--green)}
.ep-resp-block .hl-num{color:var(--orange)}
.ep-badge{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:4px;font-size:.72rem;font-weight:600}
.ep-badge.required{background:var(--red-bg);color:var(--red)}
.ep-badge.optional{background:var(--blue-bg);color:var(--blue)}
@media(max-width:768px){.page{padding:24px 16px 60px}.ep-head{flex-direction:column;align-items:flex-start}}
</style>
</head>
<body>
<div class="page">
  <div class="page-head">
    <a href="/" class="back-link"><i class="fas fa-arrow-left"></i> 返回首页</a>
    <h1>API 文档</h1>
    <p>Khazix Skills 后端所有 API 端点参考文档。基础 URL：<code style="font-family:var(--mono);font-size:.86rem;background:var(--bg2);padding:2px 8px;border-radius:4px;border:1px solid var(--border)">http://localhost:8000</code></p>
  </div>

  <!-- System -->
  <div class="cat-section">
    <div class="cat-title"><span class="cat-icon bg-blue"><i class="fas fa-server"></i></span> System</div>

    <div class="ep-card">
      <div class="ep-head"><span class="ep-method get">GET</span><span class="ep-path">/api/health</span></div>
      <div class="ep-desc">健康检查，返回服务状态、版本号、Skills 数量和运行时间。</div>
      <div class="ep-section">
        <div class="ep-section-title">curl 示例</div>
        <div class="ep-code-block"><span class="hl-method">curl</span> <span class="hl-url">http://localhost:8000/api/health</span></div>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">响应示例</div>
        <div class="ep-resp-block">{
  <span class="hl-key">"status"</span>: <span class="hl-str">"ok"</span>,
  <span class="hl-key">"version"</span>: <span class="hl-str">"2.0.0"</span>,
  <span class="hl-key">"skills_count"</span>: <span class="hl-num">8</span>,
  <span class="hl-key">"uptime_seconds"</span>: <span class="hl-num">123.4</span>
}</div>
      </div>
    </div>
  </div>

  <!-- Skills -->
  <div class="cat-section">
    <div class="cat-title"><span class="cat-icon bg-purple"><i class="fas fa-cube"></i></span> Skills</div>

    <div class="ep-card">
      <div class="ep-head"><span class="ep-method get">GET</span><span class="ep-path">/api/skills</span></div>
      <div class="ep-desc">返回所有 8 个 Skill 的元数据列表。</div>
      <div class="ep-section">
        <div class="ep-section-title">curl 示例</div>
        <div class="ep-code-block"><span class="hl-method">curl</span> <span class="hl-url">http://localhost:8000/api/skills</span></div>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">响应示例</div>
        <div class="ep-resp-block">[
  {
    <span class="hl-key">"id"</span>: <span class="hl-str">"storage-analyzer"</span>,
    <span class="hl-key">"name"</span>: <span class="hl-str">"存储分析"</span>,
    <span class="hl-key">"english_name"</span>: <span class="hl-str">"Storage Analyzer"</span>,
    <span class="hl-key">"icon"</span>: <span class="hl-str">"💾"</span>,
    <span class="hl-key">"description"</span>: <span class="hl-str">"..."</span>,
    <span class="hl-key">"one_liner"</span>: <span class="hl-str">"..."</span>,
    <span class="hl-key">"tags"</span>: [...],
    <span class="hl-key">"trigger_words"</span>: [...],
    <span class="hl-key">"platform"</span>: [...],
    <span class="hl-key">"agent_compatibility"</span>: [...],
    <span class="hl-key">"skill_md_path"</span>: <span class="hl-str">"storage-analyzer/SKILL.md"</span>
  },
  ...
]</div>
      </div>
    </div>

    <div class="ep-card">
      <div class="ep-head"><span class="ep-method get">GET</span><span class="ep-path">/api/skills/{skill_id}</span></div>
      <div class="ep-desc">返回单个 Skill 的详情，包含 SKILL.md 文件内容。</div>
      <div class="ep-section">
        <div class="ep-section-title">参数</div>
        <table class="ep-table">
          <tr><th>参数</th><th>位置</th><th>必填</th><th>说明</th></tr>
          <tr><td>skill_id</td><td>Path</td><td><span class="ep-badge required">必填</span></td><td>Skill ID，如 <code>storage-analyzer</code>、<code>aihot</code></td></tr>
        </table>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">curl 示例</div>
        <div class="ep-code-block"><span class="hl-method">curl</span> <span class="hl-url">http://localhost:8000/api/skills/storage-analyzer</span></div>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">响应示例</div>
        <div class="ep-resp-block">{
  <span class="hl-key">"id"</span>: <span class="hl-str">"storage-analyzer"</span>,
  <span class="hl-key">"name"</span>: <span class="hl-str">"存储分析"</span>,
  ...,
  <span class="hl-key">"skill_md_content"</span>: <span class="hl-str">"# Storage Analyzer\\n..."</span>
}</div>
      </div>
    </div>
  </div>

  <!-- AI HOT -->
  <div class="cat-section">
    <div class="cat-title"><span class="cat-icon bg-orange"><i class="fas fa-fire"></i></span> AI HOT</div>

    <div class="ep-card">
      <div class="ep-head"><span class="ep-method get">GET</span><span class="ep-path">/api/aihot/daily</span></div>
      <div class="ep-desc">代理 aihot 日报 API，返回今日 AI 日报。</div>
      <div class="ep-section">
        <div class="ep-section-title">curl 示例</div>
        <div class="ep-code-block"><span class="hl-method">curl</span> <span class="hl-url">http://localhost:8000/api/aihot/daily</span></div>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">响应示例</div>
        <div class="ep-resp-block">{
  <span class="hl-key">"date"</span>: <span class="hl-str">"2025-06-10"</span>,
  <span class="hl-key">"lead"</span>: { <span class="hl-key">"title"</span>: <span class="hl-str">"..."</span>, <span class="hl-key">"leadParagraph"</span>: <span class="hl-str">"..."</span> },
  <span class="hl-key">"sections"</span>: [
    { <span class="hl-key">"label"</span>: <span class="hl-str">"模型发布"</span>, <span class="hl-key">"items"</span>: [...] },
    ...
  ]
}</div>
      </div>
    </div>

    <div class="ep-card">
      <div class="ep-head"><span class="ep-method get">GET</span><span class="ep-path">/api/aihot/items</span></div>
      <div class="ep-desc">代理 aihot 条目 API，默认返回精选条目（mode=selected, take=20）。</div>
      <div class="ep-section">
        <div class="ep-section-title">参数</div>
        <table class="ep-table">
          <tr><th>参数</th><th>位置</th><th>必填</th><th>说明</th></tr>
          <tr><td>mode</td><td>Query</td><td><span class="ep-badge optional">可选</span></td><td><code>selected</code>（默认精选）、<code>all</code>（全部）</td></tr>
          <tr><td>take</td><td>Query</td><td><span class="ep-badge optional">可选</span></td><td>每页条数，默认 20</td></tr>
          <tr><td>category</td><td>Query</td><td><span class="ep-badge optional">可选</span></td><td>分类过滤：<code>ai-models</code>、<code>ai-products</code>、<code>industry</code>、<code>paper</code>、<code>tip</code></td></tr>
          <tr><td>cursor</td><td>Query</td><td><span class="ep-badge optional">可选</span></td><td>分页游标，来自上次响应的 <code>nextCursor</code></td></tr>
          <tr><td>q</td><td>Query</td><td><span class="ep-badge optional">可选</span></td><td>搜索关键词</td></tr>
        </table>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">curl 示例</div>
        <div class="ep-code-block"><span class="hl-comment"># 获取精选条目</span>
<span class="hl-method">curl</span> <span class="hl-url">http://localhost:8000/api/aihot/items</span>

<span class="hl-comment"># 按分类过滤</span>
<span class="hl-method">curl</span> <span class="hl-url">"http://localhost:8000/api/aihot/items?category=ai-models"</span>

<span class="hl-comment"># 搜索</span>
<span class="hl-method">curl</span> <span class="hl-url">"http://localhost:8000/api/aihot/items?q=OpenAI"</span></div>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">响应示例</div>
        <div class="ep-resp-block">{
  <span class="hl-key">"items"</span>: [
    {
      <span class="hl-key">"title"</span>: <span class="hl-str">"..."</span>,
      <span class="hl-key">"url"</span>: <span class="hl-str">"..."</span>,
      <span class="hl-key">"category"</span>: <span class="hl-str">"ai-models"</span>,
      <span class="hl-key">"source"</span>: <span class="hl-str">"..."</span>,
      <span class="hl-key">"summary"</span>: <span class="hl-str">"..."</span>,
      <span class="hl-key">"publishedAt"</span>: <span class="hl-str">"2025-06-10T08:00:00Z"</span>
    }
  ],
  <span class="hl-key">"hasNext"</span>: <span class="hl-num">true</span>,
  <span class="hl-key">"nextCursor"</span>: <span class="hl-str">"eyJ..."</span>
}</div>
      </div>
    </div>

    <div class="ep-card">
      <div class="ep-head"><span class="ep-method get">GET</span><span class="ep-path">/api/aihot/dailies</span></div>
      <div class="ep-desc">代理 aihot 日报归档列表，返回历史日报列表。</div>
      <div class="ep-section">
        <div class="ep-section-title">curl 示例</div>
        <div class="ep-code-block"><span class="hl-method">curl</span> <span class="hl-url">http://localhost:8000/api/aihot/dailies</span></div>
      </div>
    </div>

    <div class="ep-card">
      <div class="ep-head"><span class="ep-method get">GET</span><span class="ep-path">/api/aihot/search</span></div>
      <div class="ep-desc">代理 aihot 搜索 API，按关键词搜索 AI 资讯。</div>
      <div class="ep-section">
        <div class="ep-section-title">参数</div>
        <table class="ep-table">
          <tr><th>参数</th><th>位置</th><th>必填</th><th>说明</th></tr>
          <tr><td>q</td><td>Query</td><td><span class="ep-badge required">必填</span></td><td>搜索关键词</td></tr>
        </table>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">curl 示例</div>
        <div class="ep-code-block"><span class="hl-method">curl</span> <span class="hl-url">"http://localhost:8000/api/aihot/search?q=RAG"</span></div>
      </div>
    </div>
  </div>

  <!-- Storage -->
  <div class="cat-section">
    <div class="cat-title"><span class="cat-icon bg-green"><i class="fas fa-hard-drive"></i></span> Storage</div>

    <div class="ep-card">
      <div class="ep-head"><span class="ep-method post">POST</span><span class="ep-path">/api/scan</span></div>
      <div class="ep-desc">执行磁盘存储扫描，返回扫描结果 JSON。扫描结果会缓存，供 /api/report 使用。限流：5 次/分钟。</div>
      <div class="ep-section">
        <div class="ep-section-title">curl 示例</div>
        <div class="ep-code-block"><span class="hl-method">curl</span> -X POST <span class="hl-url">http://localhost:8000/api/scan</span></div>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">响应示例</div>
        <div class="ep-resp-block">{
  <span class="hl-key">"generated_at"</span>: <span class="hl-str">"2025-06-10 12:00:00"</span>,
  <span class="hl-key">"scan_seconds"</span>: <span class="hl-num">3.2</span>,
  <span class="hl-key">"system"</span>: {
    <span class="hl-key">"os"</span>: <span class="hl-str">"Windows 10"</span>,
    <span class="hl-key">"disk_total"</span>: <span class="hl-str">"200.0 GB"</span>,
    <span class="hl-key">"disk_used"</span>: <span class="hl-str">"156.3 GB"</span>,
    <span class="hl-key">"disk_free"</span>: <span class="hl-str">"43.7 GB"</span>,
    ...
  },
  <span class="hl-key">"groups"</span>: {
    <span class="hl-key">"user_profile"</span>: [...],
    <span class="hl-key">"appdata_local"</span>: [...],
    <span class="hl-key">"dev_caches"</span>: [...]
  }
}</div>
      </div>
    </div>

    <div class="ep-card">
      <div class="ep-head"><span class="ep-method get">GET</span><span class="ep-path">/api/report</span></div>
      <div class="ep-desc">返回存储分析交互式 HTML 报告页面。需先执行 POST /api/scan。</div>
      <div class="ep-section">
        <div class="ep-section-title">curl 示例</div>
        <div class="ep-code-block"><span class="hl-comment"># 先扫描</span>
<span class="hl-method">curl</span> -X POST <span class="hl-url">http://localhost:8000/api/scan</span>

<span class="hl-comment"># 再查看报告</span>
<span class="hl-method">curl</span> <span class="hl-url">http://localhost:8000/api/report</span></div>
      </div>
    </div>
  </div>

  <!-- Tools -->
  <div class="cat-section">
    <div class="cat-title"><span class="cat-icon bg-red"><i class="fas fa-wrench"></i></span> Tools</div>

    <div class="ep-card">
      <div class="ep-head"><span class="ep-method post">POST</span><span class="ep-path">/api/webapp-test</span></div>
      <div class="ep-desc">对目标 URL 执行 Web 应用健康检查，包括 HTTP 状态、响应时间、SSL 证书、安全头、坏链检测。限流：5 次/分钟。</div>
      <div class="ep-section">
        <div class="ep-section-title">请求体</div>
        <table class="ep-table">
          <tr><th>参数</th><th>类型</th><th>必填</th><th>说明</th></tr>
          <tr><td>url</td><td>string</td><td><span class="ep-badge required">必填</span></td><td>目标网站 URL，仅支持 http/https，禁止访问私有地址</td></tr>
        </table>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">curl 示例</div>
        <div class="ep-code-block"><span class="hl-method">curl</span> -X POST <span class="hl-url">http://localhost:8000/api/webapp-test</span> \\
  -H <span class="hl-str">"Content-Type: application/json"</span> \\
  -d <span class="hl-str">'{"url": "https://example.com"}'</span></div>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">响应示例</div>
        <div class="ep-resp-block">{
  <span class="hl-key">"url"</span>: <span class="hl-str">"https://example.com"</span>,
  <span class="hl-key">"score"</span>: <span class="hl-num">78</span>,
  <span class="hl-key">"http_status"</span>: { <span class="hl-key">"code"</span>: <span class="hl-num">200</span>, <span class="hl-key">"ok"</span>: <span class="hl-num">true</span> },
  <span class="hl-key">"response_time"</span>: { <span class="hl-key">"ms"</span>: <span class="hl-num">342</span>, <span class="hl-key">"rating"</span>: <span class="hl-str">"fast"</span> },
  <span class="hl-key">"ssl"</span>: { <span class="hl-key">"valid"</span>: <span class="hl-num">true</span>, <span class="hl-key">"expiry_date"</span>: <span class="hl-str">"2027-01-15"</span> },
  <span class="hl-key">"security_headers"</span>: { ... },
  <span class="hl-key">"broken_links"</span>: { <span class="hl-key">"count"</span>: <span class="hl-num">2</span>, <span class="hl-key">"links"</span>: [...] },
  <span class="hl-key">"page_size"</span>: { <span class="hl-key">"bytes"</span>: <span class="hl-num">245760</span>, <span class="hl-key">"human"</span>: <span class="hl-str">"240 KB"</span> }
}</div>
      </div>
    </div>

    <div class="ep-card">
      <div class="ep-head"><span class="ep-method post">POST</span><span class="ep-path">/api/security-scan</span></div>
      <div class="ep-desc">对目标 URL 执行安全扫描，检测信息泄露、不安全响应头、混合内容、过期 SSL 等。限流：5 次/分钟。</div>
      <div class="ep-section">
        <div class="ep-section-title">请求体</div>
        <table class="ep-table">
          <tr><th>参数</th><th>类型</th><th>必填</th><th>说明</th></tr>
          <tr><td>url</td><td>string</td><td><span class="ep-badge required">必填</span></td><td>目标网站 URL，仅支持 http/https，禁止访问私有地址</td></tr>
        </table>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">curl 示例</div>
        <div class="ep-code-block"><span class="hl-method">curl</span> -X POST <span class="hl-url">http://localhost:8000/api/security-scan</span> \\
  -H <span class="hl-str">"Content-Type: application/json"</span> \\
  -d <span class="hl-str">'{"url": "https://example.com"}'</span></div>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">响应示例</div>
        <div class="ep-resp-block">{
  <span class="hl-key">"url"</span>: <span class="hl-str">"https://example.com"</span>,
  <span class="hl-key">"grade"</span>: <span class="hl-str">"B"</span>,
  <span class="hl-key">"findings"</span>: [
    {
      <span class="hl-key">"title"</span>: <span class="hl-str">"缺少 Referrer-Policy 头"</span>,
      <span class="hl-key">"description"</span>: <span class="hl-str">"..."</span>,
      <span class="hl-key">"severity"</span>: <span class="hl-str">"warning"</span>
    }
  ]
}</div>
      </div>
    </div>

    <div class="ep-card">
      <div class="ep-head"><span class="ep-method post">POST</span><span class="ep-path">/api/changelog</span></div>
      <div class="ep-desc">从 Git 历史自动生成变更日志，支持 Conventional Commits 解析和版本分类。</div>
      <div class="ep-section">
        <div class="ep-section-title">请求体</div>
        <table class="ep-table">
          <tr><th>参数</th><th>类型</th><th>必填</th><th>说明</th></tr>
          <tr><td>repo_path</td><td>string</td><td><span class="ep-badge optional">可选</span></td><td>Git 仓库路径，默认当前项目</td></tr>
          <tr><td>from_ref</td><td>string</td><td><span class="ep-badge optional">可选</span></td><td>起始引用，如 <code>v1.0.0</code></td></tr>
          <tr><td>to_ref</td><td>string</td><td><span class="ep-badge optional">可选</span></td><td>结束引用，如 <code>v2.0.0</code> 或 <code>HEAD</code></td></tr>
        </table>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">curl 示例</div>
        <div class="ep-code-block"><span class="hl-comment"># 默认生成当前项目 Changelog</span>
<span class="hl-method">curl</span> -X POST <span class="hl-url">http://localhost:8000/api/changelog</span> \\
  -H <span class="hl-str">"Content-Type: application/json"</span>

<span class="hl-comment"># 指定版本范围</span>
<span class="hl-method">curl</span> -X POST <span class="hl-url">http://localhost:8000/api/changelog</span> \\
  -H <span class="hl-str">"Content-Type: application/json"</span> \\
  -d <span class="hl-str">'{"from_ref": "v1.0.0", "to_ref": "v2.0.0"}'</span></div>
      </div>
      <div class="ep-section">
        <div class="ep-section-title">响应示例</div>
        <div class="ep-resp-block">{
  <span class="hl-key">"changelog"</span>: <span class="hl-str">"# Changelog\\n\\n## [2.0.0]\\n\\n### Features\\n- ..."</span>,
  <span class="hl-key">"format"</span>: <span class="hl-str">"markdown"</span>
}</div>
      </div>
    </div>
  </div>

  <div style="text-align:center;padding-top:40px;border-top:1px solid var(--border);color:var(--text3);font-size:.82rem">
    Khazix Skills API &middot; MIT License &middot; <a href="https://github.com/KKKKhazix/khazix-skills" target="_blank">GitHub</a>
  </div>
</div>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
</body>
</html>"""


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), SkillHandler)
    print(f"Khazix Skills 服务器已启动: http://localhost:{PORT}/")
    print(f"  GET  /                       — 首页")
    print(f"  GET  /api/health             — 健康检查")
    print(f"  GET  /api/skills             — Skills 列表")
    print(f"  GET  /api/skills/{{skill_id}}  — Skill 详情")
    print(f"  GET  /api/docs               — API 文档")
    print(f"  GET  /api/aihot/daily        — AI HOT 日报")
    print(f"  GET  /api/aihot/items        — AI HOT 条目")
    print(f"  GET  /api/aihot/dailies      — AI HOT 日报归档")
    print(f"  GET  /api/aihot/search?q=kw  — AI HOT 搜索")
    print(f"  POST /api/scan               — 执行存储扫描")
    print(f"  GET  /api/report             — 存储分析报告")
    print(f"  POST /api/webapp-test        — Web 应用测试")
    print(f"  POST /api/security-scan      — 安全扫描")
    print(f"  POST /api/changelog          — 变更日志生成")
    print(f"按 Ctrl+C 停止服务")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
        server.server_close()


if __name__ == "__main__":
    main()
