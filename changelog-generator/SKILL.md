---
name: changelog-generator
description: >
  Git 变更日志自动生成 Skill。让 Agent 从 git 历史自动生成用户友好的 Changelog，
  支持 Conventional Commits 解析、版本分类、Breaking Changes 高亮。MUST trigger
  when the user says: "生成变更日志", "changelog", "版本更新记录", "release notes",
  "生成changelog", "更新日志", "版本记录", "发版说明", "生成release notes",
  "commit历史整理", or any phrase suggesting the need to generate a changelog
  from git history. Also trigger when the user mentions version releases,
  tagging, or wants a summary of recent changes. Cross-platform: works on
  Claude Code, Codex, OpenCode, Cursor.
---

# 变更日志生成器

从 git 历史自动生成用户友好的 Changelog，支持 Conventional Commits 和版本分类。

## 什么时候用

用户想要从 git 仓库的提交历史生成变更日志时使用。覆盖以下场景：

| 用户在说 | 做什么 |
|---|---|
| "生成变更日志" / "changelog" | 从最新 tag 到 HEAD 生成 |
| "版本更新记录" | 从最新 tag 到 HEAD 生成 |
| "release notes" | 从最新 tag 到 HEAD 生成 |
| "从 v1.0 到 v2.0 的变更" | 指定范围生成 |
| "最近 20 条提交的 changelog" | 指定数量生成 |
| "发版说明" | 从最新 tag 到 HEAD 生成 |

## 执行流程

### Step 1 运行变更日志生成脚本

```bash
# 默认：从最新 tag 到 HEAD
python3 scripts/gen_changelog.py

# 指定范围
python3 scripts/gen_changelog.py --from v1.0.0 --to v2.0.0

# 指定范围（ref）
python3 scripts/gen_changelog.py --from abc1234 --to HEAD

# 输出到文件
python3 scripts/gen_changelog.py --from v1.0.0 --output CHANGELOG.md
```

脚本自动完成以下工作：

1. **获取提交历史**：运行 `git log` 获取指定范围内的提交
2. **解析 Conventional Commits**：识别 `feat:`, `fix:`, `docs:`, `style:`, `refactor:`, `perf:`, `test:`, `chore:`, `ci:`, `build:` 等类型前缀
3. **Breaking Changes 高亮**：识别 `!:` 或 `BREAKING CHANGE:` 标记
4. **分类汇总**：按类型分组，生成 Markdown 格式的 Changelog

### Step 2 审查和补充

脚本生成的 Changelog 是基础版本，Agent 应该：

1. **审查分类准确性**：确认每个 commit 被正确分类
2. **补充上下文**：对重要变更添加简短说明
3. **合并同类项**：将相关的小变更合并为一条
4. **标记 Breaking Changes**：确保破坏性变更被突出标记
5. **添加版本号和日期**：如果用户指定了版本号，添加到 Changelog 头部

### Step 3 输出最终 Changelog

输出格式参考：

```markdown
# Changelog

## [v2.0.0] - 2026-06-11

### 💥 Breaking Changes
- 重构 API 响应格式，所有端点返回数据结构变更

### ✨ Features
- 新增用户导出功能
- 支持批量操作 API

### 🐛 Bug Fixes
- 修复登录超时问题
- 修复分页计算错误

### 📝 Documentation
- 更新 API 文档

### 🔧 Chore
- 升级依赖版本
```

## Conventional Commits 类型映射

| 前缀 | 分类 | 图标 |
|---|---|---|
| `feat:` | Features | ✨ |
| `fix:` | Bug Fixes | 🐛 |
| `docs:` | Documentation | 📝 |
| `style:` | Style | 💄 |
| `refactor:` | Refactoring | ♻️ |
| `perf:` | Performance | ⚡ |
| `test:` | Tests | ✅ |
| `chore:` | Chore | 🔧 |
| `ci:` | CI | 👷 |
| `build:` | Build | 📦 |
| 无前缀 | Other | 🔀 |

## 依赖与运行前提

- **Python 3 标准库**，零第三方依赖（不用 pip install）
- 需要 `git` 命令可用，且在 git 仓库目录下运行
- 跨平台：Windows / macOS / Linux 均可运行
- Windows 上命令为 `python` 或 `py -3`（不是 `python3`）

## Agent 兼容性

- Claude Code
- OpenAI Codex
- OpenCode
- Cursor

## 触发词

"生成变更日志", "changelog", "版本更新记录", "release notes"

## 标签

Git, 变更日志, 版本管理, 自动化
