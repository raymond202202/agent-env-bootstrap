# agent-env-bootstrap

AI Agent 落地环境自检与补齐工具。

裸机刚装上 AI Agent（Claude Code / Codex / Cursor / Comate 等）的第一周，Agent 想调用的
工具（`python` / `git` / `node` / 包管理器 / POSIX 工具链）机器上往往没有。
通常的表现是：**命令报错 → Agent 猜一个原因 → 试错 → 再报错**，几轮下来把迭代额度
花在了"确认环境缺什么"上。

这个工具把那个被动过程换成：**先检测 → 出缺口清单 → 按计划补齐**。

## 设计哲学

和 [ferry](https://github.com/raymond202202/ferry) 一致：**三态输出 + 不猜 + 不静默降级**。

每个依赖项的结果只可能是三种之一：

| 状态 | 含义 |
|---|---|
| `ready` | 已找到，给出**实际路径**和版本 |
| `missing` | 确认不存在，给出该平台的安装建议 |
| `check` | **无法确定**（如仅在有 tty 时可用、需要交互确认）——单独成一种状态，绝不二选一地猜 |

第三条是关键：很多工具"存在但不可用"（命令在 PATH 但依赖库缺失、需要 sudo、
WSL 下宿主机装了但 guest 没装）。把这类情况硬塞进 `ready` 或 `missing` 都会误导 Agent。

## 用法

```bash
# 扫描现有工具链
python3 scripts/envdoctor.py detect

# 输出缺口清单与安装建议
python3 scripts/envdoctor.py plan

# 数据完整性自检（改完 data/ 后跑）
python3 scripts/envdoctor.py doctor
```

数据驱动：依赖需求表在 `data/10-core.jsonc`（13 项 / 4 画像 / 4 平台）。
改数据不用改代码。

## 为什么是单文件 + 零依赖

目标场景是**裸机器**——可能连 `pip` 都没有，装第三方库无从谈起。
所以 `scripts/envdoctor.py` 只用 Python 3.8+ 标准库，单文件可整体拷走运行。

## 包内结构

```
scripts/envdoctor.py     核心引擎（单文件，Python 3.8+，零第三方依赖）
data/10-core.jsonc       依赖需求表（13 项 / 4 画像 / 4 平台）
tests/test_envdoctor.py  单元测试
SKILL.md                 Agent 技能定义（何时该调用本工具）
```

## 作为 Skill 使用

`SKILL.md` 是该工具的技能定义，可在支持 Skill 机制的 Agent 里直接加载。
触发条件：会话开始、命令失败怀疑缺工具、用户说"检查环境 / 补齐环境 / 装依赖 / 新机器初始化"。

## 测试

```bash
python3 -B tests/test_envdoctor.py
```

## License

MIT
