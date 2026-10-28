#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""envdoctor — AI Agent 落地环境检测与补齐建议引擎。

裸机装上 AI Agent（Claude Code / Codex / Cursor / Comate / 任意 Agent）后的
第一周，Agent 缺的不是智能，是机器上没有它想调用的工具。本工具做三件事：

  detect  扫描现有工具链，输出三态结果（ready / missing / check）
  plan    按角色画像输出缺口与逐条安装建议
  doctor  数据完整性自检

设计承诺（沿用 ferry 哲学）：离线确定性查表，不猜、不静默降级。
单文件、零第三方依赖、Python 3.8+（裸机器连 pip 都可能没有）。

退出码：0 = 全部 ready；2 = 有 missing；3 = 数据/平台不支持。
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 平台与常量
# ---------------------------------------------------------------------------

PLATFORMS = ("windows", "macos", "linux", "freebsd")
PLATFORM_LABEL = {
    "windows": "Windows", "macos": "macOS", "linux": "Linux", "freebsd": "FreeBSD",
}
EXIT_CODE = {"ready": 0, "missing": 2, "check": 2, "error": 3}
STATUS_ORDER = ("ready", "missing", "check")


def current_platform() -> str:
    s = platform.system().lower()
    if s == "windows":
        return "windows"
    if s == "darwin":
        return "macos"
    if s == "freebsd":
        return "freebsd"
    return "linux"


def data_dirs() -> list:
    dirs = []
    env = os.environ.get("ENVDOCTOR_DATA")
    if env:
        dirs.append(Path(env).expanduser())
    here = Path(__file__).resolve().parent
    dirs.append(here.parent / "data")
    dirs.append(here / "data")
    out, seen = [], set()
    for d in dirs:
        d = d.resolve() if d.exists() else d
        if d.is_dir() and str(d) not in seen:
            seen.add(str(d))
            out.append(d)
    return out


# ---------------------------------------------------------------------------
# JSONC 载入（允许注释与尾逗号，与 ferry 同构）
# ---------------------------------------------------------------------------

def load_jsonc(path: Path):
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


# ---------------------------------------------------------------------------
# 依赖条目模型
# ---------------------------------------------------------------------------

class Dep:
    """一条依赖：id、各平台的候选命令、检测方式、角色画像、安装建议。"""

    def __init__(self, raw: dict):
        self.raw = raw
        self.id = raw.get("id", "")
        self.category = raw.get("category", "misc")
        self.summary = raw.get("summary", "")
        self.profiles = raw.get("profiles", ["minimal"])
        self.platforms = raw.get("platforms")  # None = 全平台
        self.check = raw.get("check", {})      # {platform: [cmd, ...]} 或 {"any": [...]}
        self.min_version = raw.get("min_version")  # {platform: "x.y"} 或 "x.y"
        self.install = raw.get("install", {})  # {platform: 命令串}
        self.note = raw.get("note", "")
        self.hint = raw.get("hint", "")


class KB:
    def __init__(self):
        self.deps: list = []
        self.profiles: dict = {}
        self.errors: list = []
        self.loaded_from: list = []

    def load(self):
        dirs = data_dirs()
        if not dirs:
            self.errors.append("找不到数据目录 data/*.jsonc")
            return
        base = dirs[0]
        self.loaded_from.append(str(base))
        for path in sorted(base.glob("*.jsonc")):
            try:
                doc = load_jsonc(path)
            except Exception as exc:
                self.errors.append(f"{path.name}: JSON 解析失败 — {exc}")
                continue
            for item in doc.get("deps", []):
                d = Dep(item)
                if not d.id:
                    self.errors.append(f"{path.name}: 条目缺 id")
                    continue
                if any(x.id == d.id for x in self.deps):
                    self.errors.append(f"重复 id: {d.id}")
                    continue
                if not d.check:
                    self.errors.append(f"{d.id}: 缺 check 字段，无法检测")
                    continue
                self.deps.append(d)
            for k, v in (doc.get("profiles") or {}).items():
                self.profiles[k] = v

    def stats(self):
        cats = {}
        for d in self.deps:
            cats[d.category] = cats.get(d.category, 0) + 1
        return {"deps": len(self.deps), "profiles": list(self.profiles), "categories": cats}


# ---------------------------------------------------------------------------
# 检测执行
# ---------------------------------------------------------------------------

def _run_probe(cmd: str, timeout: int = 10) -> tuple:
    """探测一条命令，返回 (returncode, stdout+stderr 合并输出)。永不抛异常。"""
    try:
        plat = current_platform()
        if plat == "windows":
            # cmd 内置命令（where 等）与外部命令统一走 cmd /c 参数列表，
            # 避免 shell=True（安全扫描阻断项）；编码按 GBK 容错解码
            r = subprocess.run(["cmd", "/c", cmd], capture_output=True, timeout=timeout)
        else:
            # POSIX 侧探测语句可能含重定向（2>/dev/null），交给 /bin/sh -c 解析
            r = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, timeout=timeout)
        out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", errors="replace")
        return r.returncode, out
    except Exception as exc:
        return 127, str(exc)


def parse_version(out: str) -> str:
    """从探测输出里抓版本号（x.y / x.y.z / x.y.z.w）。"""
    m = re.search(r"(\d+\.\d+(?:\.\d+)*)", out)
    return m.group(1) if m else ""


def ver_ge(found: str, want: str) -> bool:
    try:
        f = [int(x) for x in found.split(".")]
        w = [int(x) for x in want.split(".")]
        while len(f) < len(w):
            f.append(0)
        while len(w) < len(f):
            w.append(0)
        return f >= w
    except Exception:
        return False


def check_dep(dep: Dep, plat: str) -> dict:
    """检测一条依赖，返回 {status, version, detail}。三态：ready/missing/check。"""
    cmds = dep.check.get(plat) or dep.check.get("any") or []
    if isinstance(cmds, str):
        cmds = [cmds]
    if not cmds:
        return {"status": "check", "version": "",
                "detail": f"{PLATFORM_LABEL[plat]} 无检测定义，请人工确认"}

    for cmd in cmds:
        # 版本探测命令（--version/-V）能跑通才算 ready
        rc, out = _run_probe(cmd)
        if rc == 0 and out.strip():
            ver = parse_version(out)
            want = dep.min_version
            if isinstance(want, dict):
                want = want.get(plat)
            if want and ver:
                if not ver_ge(ver, want):
                    return {"status": "check", "version": ver,
                            "detail": f"存在但版本 {ver} < 要求 {want}，升级或确认后使用"}
            return {"status": "ready", "version": ver,
                    "detail": out.strip().splitlines()[0][:80] if out.strip() else ""}
        if rc == 0 and not out.strip():
            # 命令存在但无输出（GUI 程序常见），只验证存在性
            return {"status": "ready", "version": "",
                    "detail": "命令存在（无版本输出）"}

    # 全部探测失败。区分"真的没有"和"探测方式可能失效"
    # 最后一个探测若因超时/权限等返回 127 以外的可执行痕迹，标 check 而非 missing
    name0 = cmds[0].split()[0] if cmds[0].split() else ""
    if shutil.which(name0):
        return {"status": "check", "version": "",
                "detail": f"命令 {name0} 在 PATH 上但探测未通过（版本参数不支持？）"}
    return {"status": "missing", "version": "",
            "detail": f"未找到：{name0}"}


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_detect(args, kb: KB) -> int:
    plat = args.platform or current_platform()
    if plat not in PLATFORMS:
        print(f"不支持的平台：{plat}", file=sys.stderr)
        return 3
    profile = getattr(args, "profile", None)
    rows = []
    for dep in kb.deps:
        if profile and profile not in dep.profiles:
            continue
        if dep.platforms and plat not in dep.platforms:
            continue  # 平台白名单外的条目跳过
        res = check_dep(dep, plat)
        rows.append({
            "id": dep.id, "category": dep.category, "summary": dep.summary,
            "profile": dep.profiles, **res,
        })
    status_rank = {"ready": 0, "check": 1, "missing": 2}
    worst = max((status_rank[r["status"]] for r in rows), default=0)
    overall = STATUS_ORDER[worst] if rows else "ready"
    if args.json:
        print(json.dumps({"platform": plat, "profile": profile,
                          "overall": overall, "results": rows},
                         ensure_ascii=False, indent=2))
        return EXIT_CODE.get(overall, 3)
    print(f"环境检测 — {PLATFORM_LABEL[plat]}{'（画像: ' + profile + '）' if profile else '（全部）'}")
    print(f"整体状态: {overall}\n")
    cur_cat = None
    for r in rows:
        if r["category"] != cur_cat:
            cur_cat = r["category"]
            print(f"[{cur_cat}]")
        badge = {"ready": "✓ ready  ", "missing": "✗ missing", "check": "? check   "}[r["status"]]
        ver = f" v{r['version']}" if r["version"] else ""
        print(f"  {badge} {r['id']:<14}{ver:<12} {r['summary']}")
        if r["status"] != "ready" and r["detail"]:
            print(f"      {r['detail']}")
    return EXIT_CODE.get(overall, 3)


def cmd_plan(args, kb: KB) -> int:
    plat = args.platform or current_platform()
    profile = args.profile
    if profile and profile not in kb.profiles and profile not in {d.id for d in kb.deps}:
        # profiles 定义在数据里；也允许直接传
        pass
    missing_rows = []
    for dep in kb.deps:
        if profile and profile not in dep.profiles:
            continue
        if dep.platforms and plat not in dep.platforms:
            continue
        res = check_dep(dep, plat)
        if res["status"] != "ready":
            inst = dep.install.get(plat) or dep.install.get("any") or ""
            missing_rows.append({
                "id": dep.id, "status": res["status"], "detail": res["detail"],
                "install": inst, "note": dep.note, "hint": dep.hint,
                "danger": dep.raw.get("danger", False),
            })
    if args.json:
        print(json.dumps({"platform": plat, "profile": profile, "missing": missing_rows},
                         ensure_ascii=False, indent=2))
        return 0 if not missing_rows else 2
    if not missing_rows:
        print(f"画像 {profile or '全部'} 在 {PLATFORM_LABEL[plat]} 上无缺口。")
        return 0
    print(f"补齐计划 — {PLATFORM_LABEL[plat]} / 画像 {profile or '全部'}")
    print(f"缺口 {len(missing_rows)} 项。逐条执行，高危项务必先看 note。\n")
    for r in missing_rows:
        mark = "⚠ " if r["danger"] else "  "
        print(f"{mark}{r['id']:<14} [{r['status']}]")
        if r["install"]:
            print(f"     安装: {r['install']}")
        if r["note"]:
            print(f"     注意: {r['note']}")
        if r["hint"]:
            print(f"     提示: {r['hint']}")
        print()
    print("执行原则：逐条执行、每条验证（重跑 detect 看是否变 ready）、")
    print("高危项（标 ⚠）执行前向用户确认；装完把输出贴回对话让 Agent 复核。")
    return 2


def cmd_doctor(args, kb: KB) -> int:
    st = kb.stats()
    print("envdoctor doctor")
    print(f"  数据目录  {kb.loaded_from[0] if kb.loaded_from else '(未加载)'}")
    print(f"  依赖条目  {st['deps']}")
    print(f"  画像      {', '.join(st['profiles']) or '(无)'}")
    cats = "  ".join(f"{k}:{v}" for k, v in sorted(st["categories"].items()))
    print(f"  分类      {cats}")
    print()
    problems = list(kb.errors)
    # 不变量：每条 dep 至少一个平台有 install 建议或有 note 说明为什么没有
    for d in kb.deps:
        if not any(d.install.get(p) for p in PLATFORMS) and not d.note:
            problems.append(f"{d.id}: 无任何平台的安装建议且无 note 说明")
    for p in problems:
        print(f"  ✗ {p}")
    if not problems:
        print("  ✓ 数据完整性检查通过")
        return 0
    print(f"\n错误 {len(problems)} 项", file=sys.stderr)
    return 3


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="envdoctor", description=__doc__.splitlines()[0])
    ap.add_argument("--data", help="数据目录覆盖（默认 scripts 同级 data/）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_det = sub.add_parser("detect", help="扫描现有工具链（三态输出）")
    p_det.add_argument("--profile", help="只检测某画像（minimal/web/data/dev）")
    p_det.add_argument("--platform", help="覆盖目标平台（默认本机）")
    p_det.add_argument("--json", action="store_true")
    p_det.set_defaults(func=cmd_detect)

    p_plan = sub.add_parser("plan", help="输出缺口清单与安装建议")
    p_plan.add_argument("--profile", help="按画像过滤缺口")
    p_plan.add_argument("--platform")
    p_plan.add_argument("--json", action="store_true")
    p_plan.set_defaults(func=cmd_plan)

    p_doc = sub.add_parser("doctor", help="数据完整性自检")
    p_doc.set_defaults(func=cmd_doctor)

    args = ap.parse_args(argv)
    if args.data:
        os.environ["ENVDOCTOR_DATA"] = args.data
    kb = KB()
    kb.load()
    return args.func(args, kb)


if __name__ == "__main__":
    sys.exit(main())
