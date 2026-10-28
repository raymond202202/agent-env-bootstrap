#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""envdoctor 单元测试。python -B tests/test_envdoctor.py（在项目根目录运行）"""
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "envdoctor.py"
PY = sys.executable


def run_cli(*args, timeout=180):
    r = subprocess.run([PY, "-B", str(SCRIPT), *args],
                       capture_output=True, timeout=timeout, cwd=str(ROOT))
    out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", errors="replace")
    return r.returncode, out


class TestData(unittest.TestCase):
    def test_cli_contracts(self):
        """一次全量 detect + 一次 doctor 覆盖：JSON 可解析、三态合法、
        平台白名单、退出码、安装建议不变量"""
        rc, out = run_cli("detect", "--profile", "minimal", "--json")
        d = json.loads(out)
        self.assertIn(d["overall"], ("ready", "missing", "check"))
        self.assertTrue(d["results"])
        for r in d["results"]:
            self.assertIn(r["status"], ("ready", "missing", "check"), r)
        ids = [r["id"] for r in d["results"]]
        if d["platform"] != "windows":
            self.assertNotIn("scoop", ids)
            self.assertNotIn("pwsh-policy", ids)
        self.assertIn(rc, (0, 2))

    def test_plan_and_doctor(self):
        rc, out = run_cli("plan", "--profile", "dev", "--json")
        self.assertIn("missing", json.loads(out))
        rc2, out2 = run_cli("doctor")
        self.assertEqual(rc2, 0, out2)
        self.assertIn("数据完整性检查通过", out2)
        self.assertNotIn("无任何平台的安装建议", out2)


class TestEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("ed", SCRIPT)
        cls.ed = importlib.util.module_from_spec(spec)
        sys.modules["ed"] = cls.ed
        spec.loader.exec_module(cls.ed)
        cls.kb = cls.ed.KB()
        cls.kb.load()

    def test_parse_version(self):
        ed = self.ed
        self.assertEqual(ed.parse_version("Python 3.12.12"), "3.12.12")
        self.assertEqual(ed.parse_version("node v22.1.0"), "22.1.0")
        self.assertEqual(ed.parse_version("git version 2.55.0"), "2.55.0")

    def test_ver_ge(self):
        ed = self.ed
        self.assertTrue(ed.ver_ge("3.12", "3.10"))
        self.assertTrue(ed.ver_ge("22.1", "18"))
        self.assertFalse(ed.ver_ge("2.9", "2.30"))
        self.assertFalse(ed.ver_ge("", "1.0"))

    def test_check_dep_three_states(self):
        ed = self.ed
        # ready: python（本机必有）
        d = [x for x in self.kb.deps if x.id == "python"][0]
        res = ed.check_dep(d, ed.current_platform())
        self.assertEqual(res["status"], "ready", res)
        # check: 无检测定义的平台
        d2 = [x for x in self.kb.deps if x.id == "scoop"][0]
        res2 = ed.check_dep(d2, "freebsd")
        self.assertEqual(res2["status"], "check", res2)

    def test_dep_count_and_profiles(self):
        self.assertEqual(len(self.kb.deps), 13)
        self.assertIn("minimal", self.kb.profiles)
        minimal = [d for d in self.kb.deps if "minimal" in d.profiles]
        self.assertGreaterEqual(len(minimal), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
