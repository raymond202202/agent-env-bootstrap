---
name: agent-env-bootstrap
description: Agent 落地环境自检与补齐工具（面向 AI Agent 自用，不是给用户的问答）。裸机刚装上 AI Agent（Claude Code/Codex/Cursor/Comate 等）的第一周，Agent 想调用的工具（python/git/node/包管理器/POSIX 工具链）机器上往往没有。会话开始时、命令执行失败怀疑缺工具时、用户说"检查环境""补齐环境""装依赖""新机器初始化"时，先用本技能扫描工具链再行动，避免逐条试错浪费轮次。三态输出（ready/missing/check），不猜、不静默降级，高危安装建议强制人工确认。
license: MIT
compatibility: claude-code, codex, cursor, comate
metadata:
  version: "1.0.1"
---

# Agent 环境引导（envdoctor）

裸机 + AI Agent 的"第一周鸿沟"：Agent 什么都能想，机器什么都不给。本技能把"遇到问题→试错→装工具"的被动过程换成"先检测→按计划补齐"。

**核心承诺（与 ferry 同哲学）：三态输出 + 不静默。**

## 包内结构

```
scripts/envdoctor.py   核心引擎（单文件，Python 3.8+，零第三方依赖——裸机器可能连 pip 都没有）
data/10-core.jsonc     依赖需求表（13 项 / 4 画像 / 4 平台）
tests/test_envdoctor.py 单元测试
```

## Agent 使用流程（自检时机）

**何时用：**
- 新会话开始、即将执行工具链相关任务，不确定机器配置时
- 命令执行失败、报"command not found"/"不是内部或外部命令"时
- 用户说"检查环境""补齐环境""新机器初始化""装依赖"时

**何时不用：**
- 同一会话已跑过 detect 且环境未变
- 纯文档/网页类任务（不碰本地工具链）

## 三步走

```bash
# 1) 检测（三态：ready / missing / check）
python "$SKILL_DIR/scripts/envdoctor.py" detect --profile minimal

# 2) 缺口计划（含逐条安装建议，高危项标 ⚠）
python "$SKILL_DIR/scripts/envdoctor.py" plan --profile minimal

# 3) 数据自检（改数据后必跑）
python "$SKILL_DIR/scripts/envdoctor.py" doctor
```

`--profile` 画像：`minimal`（Agent 生存底线：python/git/包管理器/POSIX 工具）→ `web`（+node/npx，MCP server）→ `data`（+pandas/numpy）→ `dev`（+编译链/WSL）。
`--json` 输出供程序化处理；`--platform` 可模拟检测其他平台（本机不执行，只出计划）。

## 三态契约（必须遵守）

| 状态 | 含义 | Agent 该怎么做 |
|---|---|---|
| `ready` | 工具存在且版本达标 | 直接用，无需再探测 |
| `missing` | 确认缺失 | 按 plan 的安装命令逐条补，装完重跑 detect 验证 |
| `check` | 存在但可疑（版本低/探测方式存疑/平台无定义） | **不要猜**，向用户说明疑点再决定 |

**永远不要**把 check 当 ready 用；**永远不要**静默跳过 missing 就继续执行——那会进入逐条试错的死循环，正是本技能要消灭的。

## 安装建议的执行规矩

1. `plan` 输出的命令**逐条执行**，每条装完重跑 `detect` 确认变 ready，再装下一条
2. **高危项（标 ⚠，如 WSL）执行前必须征得用户确认**——涉及管理员权限、重启、系统级改动
3. Windows 注意：新装工具 PATH 要新开终端才生效；PowerShell 执行策略默认拦截 .ps1（数据表已含修复建议）
4. 包管理器（pkgmgr）missing 时**先装包管理器**，它是其他一切的前提

## 各平台要点（数据表已内置，此处速查）

- **Windows**：Agent 训练记忆里的 POSIX 命令（grep/sed/unzip）裸机全没有——装 Git for Windows 一并解决；python 是 `py -3` 不是 `python3`
- **macOS**：git/make 在 Command Line Tools 里（`xcode-select --install`）；brew 是一切前提
- **Linux**：发行版碎片化，plan 按实际发行版给 apt/dnf/pacman 建议
- **FreeBSD**：pkg 唯一入口

## 与 ferry（跨平台命令防错）的关系

互补不重叠：ferry 管**写法对不对**（sed -i 差异、stat 方言），本技能管**命令存不存在**（python 装没装、grep 有没有）。两技能都加载时：先用本技能确认环境，再用 ferry 核对写法。

## 维护

改 `data/*.jsonc` 后：

```bash
python "$SKILL_DIR/scripts/envdoctor.py" doctor          # 不变量：每条依赖有安装建议或有 note 说明
python "$SKILL_DIR/tests/test_envdoctor.py"              # 6 项契约测试
```

新增依赖条目：`id`（唯一）、`profiles`（所属画像）、`check`（至少一个平台的探测命令，**探测命令必须安全无副作用**，只用 --version 类）、`install`（各平台建议，高危加 `"danger": true`）、可选 `platforms` 白名单（平台专属条目必加，否则会污染其他平台的结果）。
