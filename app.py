#!/usr/bin/env python3
"""Khazix Skills 统一 Web 服务器。

仅使用 Python 标准库，提供静态文件服务、API 端点和代理功能。

端点：
  GET  /                       → web/index.html
  GET  /api/skills             → 返回 5 个 Skill 的元数据 JSON
  GET  /api/skills/{skill_id}  → 返回单个 Skill 详情（含 SKILL.md 内容）
  GET  /api/aihot/daily        → 代理 aihot 日报 API
  GET  /api/aihot/items        → 代理 aihot 条目 API（默认 mode=selected&take=20）
  GET  /api/aihot/dailies      → 代理 aihot 日报归档列表
  GET  /api/aihot/search?q=kw  → 代理 aihot 搜索
  POST /api/scan               → 执行存储扫描，返回扫描结果 JSON
  GET  /api/report             → 返回存储分析报告 HTML 页面
  静态文件                      → web/ 目录
"""
import json
import mimetypes
import os
import subprocess
import sys
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


def _log_request(method: str, path: str, status: int = 0):
    """打印请求日志到 stdout。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    suffix = f" → {status}" if status else ""
    print(f"[{ts}] {method} {path}{suffix}", flush=True)

# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class SkillHandler(BaseHTTPRequestHandler):
    """Khazix Skills 统一请求处理器。"""

    # 静默默认日志，我们用自己的格式
    def log_message(self, format, *args):
        pass

    # ---- 通用响应辅助 ----

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
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, code, data, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, code, message):
        self._send_json(code, {"error": message})

    # ---- OPTIONS (CORS preflight) ----

    def do_OPTIONS(self):
        _log_request("OPTIONS", self.path)
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # ---- GET ----

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parsed.query

        # 首页
        if path == "" or path == "/index.html":
            _log_request("GET", self.path)
            self._serve_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return

        # API: Skills 列表
        if path == "/api/skills":
            _log_request("GET", self.path)
            self._send_json(200, SKILLS)
            return

        # API: 单个 Skill 详情
        if path.startswith("/api/skills/"):
            skill_id = path[len("/api/skills/"):]
            _log_request("GET", self.path)
            self._handle_skill_detail(skill_id)
            return

        # API: aihot 日报代理
        if path == "/api/aihot/daily":
            _log_request("GET", self.path)
            status, body, ct = _proxy_aihot("/api/public/daily", query)
            self._send_bytes(status, body, ct)
            return

        # API: aihot 条目代理
        if path == "/api/aihot/items":
            _log_request("GET", self.path)
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
            _log_request("GET", self.path)
            status, body, ct = _proxy_aihot("/api/public/dailies", query)
            self._send_bytes(status, body, ct)
            return

        # API: aihot 搜索
        if path == "/api/aihot/search":
            _log_request("GET", self.path)
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
            _log_request("GET", self.path)
            self._serve_report()
            return

        # 静态文件（web/ 目录下）
        rel_path = parsed.path.lstrip("/")
        file_path = WEB_DIR / rel_path
        if file_path.is_file() and WEB_DIR in file_path.resolve().parents:
            _log_request("GET", self.path)
            mime, _ = mimetypes.guess_type(str(file_path))
            mime = mime or "application/octet-stream"
            self._serve_file(file_path, mime)
            return

        _log_request("GET", self.path, 404)
        self._send_error(404, "Not Found")

    # ---- POST ----

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        # API: 执行存储扫描
        if path == "/api/scan":
            _log_request("POST", self.path)
            self._handle_scan()
            return

        # API: Web 应用测试
        if path == "/api/webapp-test":
            _log_request("POST", self.path)
            self._handle_webapp_test()
            return

        # API: 安全扫描
        if path == "/api/security-scan":
            _log_request("POST", self.path)
            self._handle_security_scan()
            return

        # API: 变更日志生成
        if path == "/api/changelog":
            _log_request("POST", self.path)
            self._handle_changelog()
            return

        _log_request("POST", self.path, 404)
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
        """读取 POST 请求的 JSON body。"""
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _handle_webapp_test(self):
        """执行 Web 应用健康检查。"""
        try:
            body = self._read_json_body()
            url = body.get("url", "")
            if not url:
                self._send_error(400, "Missing required parameter: url")
                return
            script = str(BASE_DIR / "webapp-testing" / "scripts" / "health_check.py")
            result = subprocess.run(
                [sys.executable, script, url],
                capture_output=True, text=True, timeout=60,
            )
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                self._send_error(500, f"脚本输出非有效 JSON: {result.stdout[:500] or result.stderr[:500]}")
                return
            self._send_json(200, data)
        except subprocess.TimeoutExpired:
            self._send_error(504, "Web 应用测试超时（60s）")
        except Exception as e:
            self._send_error(500, f"Web 应用测试失败: {e}")

    def _handle_security_scan(self):
        """执行安全扫描。"""
        try:
            body = self._read_json_body()
            url = body.get("url", "")
            if not url:
                self._send_error(400, "Missing required parameter: url")
                return
            script = str(BASE_DIR / "security-scanner" / "scripts" / "scan_security.py")
            result = subprocess.run(
                [sys.executable, script, url],
                capture_output=True, text=True, timeout=60,
            )
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                self._send_error(500, f"脚本输出非有效 JSON: {result.stdout[:500] or result.stderr[:500]}")
                return
            self._send_json(200, data)
        except subprocess.TimeoutExpired:
            self._send_error(504, "安全扫描超时（60s）")
        except Exception as e:
            self._send_error(500, f"安全扫描失败: {e}")

    def _handle_changelog(self):
        """执行变更日志生成。"""
        try:
            body = self._read_json_body()
            repo_path = body.get("repo_path", "")
            from_ref = body.get("from_ref", "")
            to_ref = body.get("to_ref", "")
            script = str(BASE_DIR / "changelog-generator" / "scripts" / "gen_changelog.py")
            cmd = [sys.executable, script]
            if repo_path:
                cmd.append(repo_path)
            if from_ref:
                cmd.append(f"--from={from_ref}")
            if to_ref:
                cmd.append(f"--to={to_ref}")
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
                cwd=str(BASE_DIR),
            )
            # changelog 脚本输出 Markdown，包装为 JSON
            output = result.stdout.strip()
            if output:
                data = {"changelog": output, "format": "markdown"}
            else:
                err = result.stderr.strip() or "No output"
                data = {"error": err, "changelog": ""}
            self._send_json(200, data)
        except subprocess.TimeoutExpired:
            self._send_error(504, "变更日志生成超时（60s）")
        except Exception as e:
            self._send_error(500, f"变更日志生成失败: {e}")


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), SkillHandler)
    print(f"Khazix Skills 服务器已启动: http://localhost:{PORT}/")
    print(f"  GET  /                       — 首页")
    print(f"  GET  /api/skills             — Skills 列表")
    print(f"  GET  /api/skills/{{skill_id}}  — Skill 详情")
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
