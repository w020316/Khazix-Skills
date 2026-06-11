---
name: webapp-testing
description: >
  Web 应用自动化测试 Skill。让 Agent 用一句话启动 Web 应用测试，自动检测页面可访问性、
  链接完整性、性能指标、安全头配置，生成 HTML 测试报告。MUST trigger when the user says:
  "测试网站", "web测试", "页面检测", "网站体检", "webapp test", "测试一下网站",
  "网站健康检查", "页面测试", "网站可用性", "检测网页", "web health check",
  or any phrase suggesting a web application needs automated testing or health checking.
  Also trigger when the user provides a URL and asks to check its accessibility,
  performance, security, or overall health. Cross-platform: works on Claude Code,
  Codex, OpenCode, Cursor.
---

# Web 应用测试

一句话启动 Web 应用全面体检，自动检测可访问性/链接/性能/安全，生成 HTML 测试报告。

## 什么时候用

用户想要对某个 Web URL 进行自动化检测时使用。覆盖以下场景：

| 用户在说 | 做什么 |
|---|---|
| "测试网站 https://example.com" | 完整健康检查 |
| "帮我检测一下这个页面" | 完整健康检查 |
| "网站体检" / "webapp test" | 完整健康检查 |
| "这个网站链接有没有坏的" | 侧重链接完整性 |
| "页面加载快不快" | 侧重性能指标 |
| "安全头配没配" | 侧重安全头检测 |

## 执行流程

### Step 1 运行健康检查脚本

```bash
python3 scripts/health_check.py <URL>
```

脚本自动完成以下检测：

1. **HTTP 状态检查**：请求目标 URL，记录状态码和响应时间
2. **SSL 证书检查**：验证证书有效性、过期时间
3. **安全头检测**：检查 X-Frame-Options、Content-Security-Policy、Strict-Transport-Security、X-Content-Type-Options
4. **链接完整性**：爬取页面中前 20 个链接，逐一检测是否可访问
5. **页面大小**：记录响应体大小

输出 JSON 结果到 stdout。

### Step 2 分析结果

拿到 JSON 结果后，按以下维度分析：

- **可访问性**：HTTP 状态码是否 2xx/3xx，响应时间是否合理（< 3s 正常，< 1s 优秀）
- **链接完整性**：有多少链接失效（4xx/5xx），失效链接的 URL 和状态码
- **性能指标**：响应时间、页面大小是否在合理范围
- **安全头**：哪些安全头缺失，每个缺失头的风险说明

### Step 3 生成报告

将分析结果整理为结构化的 Markdown 或 HTML 报告，包含：

1. **总览**：目标 URL、检测时间、整体评分（A/B/C/D/F）
2. **可访问性**：状态码、响应时间
3. **SSL 证书**：是否有效、过期时间
4. **安全头**：已配置 / 未配置列表及建议
5. **链接检查**：有效链接数 / 失效链接数 / 失效链接详情
6. **页面大小**：大小及评估
7. **改进建议**：按优先级排列的可执行建议

## 依赖与运行前提

- **Python 3 标准库**，零第三方依赖（不用 pip install）
- 跨平台：Windows / macOS / Linux 均可运行
- Windows 上命令为 `python` 或 `py -3`（不是 `python3`）

## Agent 兼容性

- Claude Code
- OpenAI Codex
- OpenCode
- Cursor

## 触发词

"测试网站", "web测试", "页面检测", "网站体检", "webapp test", "网站健康检查", "检测网页"

## 标签

测试, Web, 性能, 安全, 自动化
