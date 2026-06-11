---
name: security-scanner
description: >
  Web 应用安全扫描 Skill。让 Agent 对目标 Web 应用进行基础安全扫描，检测常见漏洞
  （信息泄露、不安全头、混合内容、过期 SSL），生成安全评估报告。MUST trigger when
  the user says: "安全扫描", "安全检测", "漏洞扫描", "security scan",
  "安全检查", "安全评估", "网站安全", "web安全检测", "检查安全头", "SSL检查",
  or any phrase suggesting a web application needs security scanning.
  Also trigger when the user provides a URL and asks about its security posture,
  vulnerability status, or SSL/TLS configuration. Cross-platform: works on
  Claude Code, Codex, OpenCode, Cursor.
---

# 安全扫描器

一句话对 Web 应用做安全体检，检测信息泄露/不安全头/混合内容/过期 SSL，生成评估报告。

## 什么时候用

用户想要对某个 Web URL 进行安全扫描时使用。覆盖以下场景：

| 用户在说 | 做什么 |
|---|---|
| "安全扫描 https://example.com" | 完整安全扫描 |
| "安全检测" / "漏洞扫描" | 完整安全扫描 |
| "security scan" | 完整安全扫描 |
| "检查安全头" | 侧重安全头检测 |
| "SSL 检查" | 侧重证书检测 |
| "这个网站有没有信息泄露" | 侧重信息泄露检测 |
| "混合内容检查" | 侧重混合内容检测 |

## 执行流程

### Step 1 运行安全扫描脚本

```bash
python3 scripts/scan_security.py <URL>
```

脚本自动完成以下检测：

1. **SSL/TLS 证书**：有效性、过期时间、剩余天数
2. **安全头**：CSP、HSTS、X-Frame-Options、X-Content-Type-Options、Referrer-Policy、Permissions-Policy
3. **信息泄露**：Server 头、X-Powered-By 头、错误页面信息泄露
4. **混合内容**：HTTPS 页面中是否引用 HTTP 资源
5. **Cookie 标志**：Secure、HttpOnly、SameSite 属性

输出 JSON 结果到 stdout，每项检测结果带严重级别（critical/warning/info）。

### Step 2 分析结果

拿到 JSON 结果后，按严重级别分析：

- **🔴 Critical**：必须立即修复的安全问题（过期 SSL、缺失 HSTS on HTTPS 站点等）
- **🟡 Warning**：建议修复的安全问题（缺失安全头、信息泄露等）
- **🟢 Info**：信息性提示（已正确配置的安全头等）

### Step 3 生成安全评估报告

将分析结果整理为结构化报告，包含：

1. **总览**：目标 URL、扫描时间、整体安全评级（A/B/C/D/F）
2. **SSL/TLS**：证书状态、过期时间、剩余天数
3. **安全头**：已配置 / 未配置列表及风险说明
4. **信息泄露**：发现的泄露信息及建议
5. **混合内容**：HTTP 资源列表
6. **Cookie 安全**：Cookie 标志配置情况
7. **修复建议**：按严重级别排列的可执行建议

## 严重级别定义

| 级别 | 含义 | 示例 |
|---|---|---|
| critical | 必须立即修复，存在被攻击风险 | SSL 证书过期、HTTPS 站点缺失 HSTS |
| warning | 建议修复，存在潜在风险 | 缺失 CSP、Server 头泄露版本信息 |
| info | 信息性提示，当前配置正确 | 已配置 HSTS、已配置 X-Frame-Options |

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

"安全扫描", "安全检测", "漏洞扫描", "security scan"

## 标签

安全, 扫描, 漏洞检测, Web安全
