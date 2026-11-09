"""N.E.K.O 适配版的离线自检（不依赖宿主 SDK，只用标准库）。

跑法（在仓库根目录）：
    python -m unittest discover -s tests -v
"""

import ast
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SPEC = importlib.util.spec_from_file_location("rbb_core", os.path.join(ROOT, "rbb_core.py"))
rbb_core = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rbb_core)

SKILL_DIR = os.path.join(ROOT, "skills", "reuse-before-build")
CONTRACT = os.path.join(SKILL_DIR, "localization.contract.json")
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")


class TestManifest(unittest.TestCase):
    def setUp(self):
        try:
            import tomllib
        except ModuleNotFoundError:  # 3.10 及以下
            self.skipTest("需要 Python 3.11+ 的 tomllib")
        with open(os.path.join(ROOT, "plugin.toml"), "rb") as fh:
            self.manifest = tomllib.load(fh)

    def test_identity_fields(self):
        p = self.manifest["plugin"]
        self.assertEqual(p["id"], "reuse_before_build")
        self.assertTrue(p["name"])
        self.assertGreater(len(p["description"]), 40)
        self.assertGreater(len(p["short_description"]), 40)
        self.assertRegex(p["version"], r"^\d+\.\d+\.\d+$")
        self.assertGreaterEqual(len(p["keywords"]), 8)

    def test_entry_names_the_class(self):
        self.assertRegex(self.manifest["plugin"]["entry"],
                         r"^plugin\.plugins\.reuse_before_build:[A-Za-z_][A-Za-z0-9_]*$")

    def test_runtime_timeout_in_range(self):
        t = self.manifest["plugin_runtime"]["timeout"]
        self.assertTrue(0 < t <= 300, t)

    def test_ui_guide_exists(self):
        guide = self.manifest["plugin"]["ui"]["guide"][0]
        self.assertTrue(os.path.isfile(os.path.join(ROOT, guide["entry"])), guide["entry"])


class TestEntryTools(unittest.TestCase):
    """入口方法必须都挂 llm_tool，否则 AI 看不到。"""

    def setUp(self):
        with open(os.path.join(ROOT, "__init__.py"), encoding="utf-8") as fh:
            self.tree = ast.parse(fh.read())

    def _tool_names(self):
        names = []
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name)
                        and dec.func.id == "llm_tool"):
                    continue
                for kw in dec.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        names.append(kw.value.value)
        return names

    def test_expected_tools_exist(self):
        got = set(self._tool_names())
        self.assertEqual(got, {"rbb_rules", "rbb_scan", "rbb_check", "rbb_verify", "rbb_status"})

    def test_tool_descriptions_and_schemas(self):
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name)
                        and dec.func.id == "llm_tool"):
                    continue
                kw = {k.arg: k.value for k in dec.keywords}
                desc = kw.get("description")
                self.assertTrue(isinstance(desc, ast.Constant) and len(str(desc.value)) > 60,
                                f"{node.name} 的 description 太短")
                params = kw.get("parameters")
                self.assertTrue(isinstance(params, ast.Dict), f"{node.name} 缺 parameters")


class TestBundledSkill(unittest.TestCase):
    def test_contract_hash_matches_block(self):
        with open(CONTRACT, encoding="utf-8") as fh:
            want = json.load(fh)["block"]["canonical_sha256"]
        block = rbb_core.core_rules_block()
        norm = "\n".join(line.rstrip() for line in block.strip().splitlines())
        self.assertEqual(hashlib.sha256(norm.encode("utf-8")).hexdigest(), want)

    def test_rules_have_five_numbered_items(self):
        nums = [int(n) for n in re.findall(r"^(\d+)\.\s", rbb_core.core_rules_block(), re.M)]
        self.assertEqual(nums, [1, 2, 3, 4, 5])

    def test_verifier_passes_on_bundled_copy(self):
        p = subprocess.run(
            [sys.executable, "-X", "utf8", rbb_core.VERIFY_SCRIPT, SKILL_MD],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("verbatim", p.stdout)
        self.assertIn("rules=[1, 2, 3, 4, 5]/5", p.stdout)

    def test_verifier_rejects_deleted_rule(self):
        with open(SKILL_MD, encoding="utf-8") as fh:
            text = fh.read()
        broken = "\n".join(line for line in text.splitlines() if "Assume it already exists" not in line)
        broken += "\nlocalized: host=test | kept=all | changed=none | unsupported=none\n"
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "SKILL.md")
            with open(dst, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(broken)
            p = subprocess.run(
                [sys.executable, "-X", "utf8", rbb_core.VERIFY_SCRIPT, dst],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("invariants_hash", p.stdout)


class TestSearchWrapper(unittest.TestCase):
    def test_missing_gh_returns_code_2(self):
        res = rbb_core.search_candidates(["anything"], gh_binary="gh-not-installed-xyz")
        self.assertEqual(res["code"], 2)
        self.assertFalse(res["ok"])
        self.assertIn("gh", res["reason"])

    def test_empty_keywords_returns_code_2(self):
        res = rbb_core.search_candidates(["", "  "])
        self.assertEqual(res["code"], 2)

    def test_default_since_survives_leap_day(self):
        import datetime as dt
        self.assertEqual(rbb_core.default_since(365, dt.date(2028, 2, 29)), "2027-03-01")


class TestDeliverable(unittest.TestCase):
    GOOD = """## 找到了什么
| 仓库 | ★ | 最近推送 |
|---|---|---|
| owner/autoclip | 4200 | 2026-10-01 |

## 为什么选它
活跃、MIT、缺字幕挑高光。

## 我要加什么
基于字幕的高光片段挑选。

## 备选与风险
备选：fork 自维护。风险：维护者响应慢。

结论：Join。
"""

    def test_good_deliverable_passes(self):
        res = rbb_core.check_deliverable(self.GOOD)
        self.assertTrue(res["ok"], res["missing"])

    def test_missing_sections_fail(self):
        res = rbb_core.check_deliverable("## 找到了什么\n随便写写。")
        self.assertFalse(res["ok"])
        self.assertIn("section:why", res["missing"])

    def test_build_requires_gate_line(self):
        text = self.GOOD.replace("结论：Join。", "结论：Build（新建）。")
        res = rbb_core.check_deliverable(text)
        self.assertFalse(res["ok"])
        self.assertIn("gate_line", res["missing"])

    def test_build_with_gate_line_passes(self):
        text = self.GOOD.replace(
            "结论：Join。",
            "不可复用原因 / Not reusable because: 许可为 GPL-3.0，与用户闭源要求冲突。\n\n结论：Build。",
        )
        res = rbb_core.check_deliverable(text)
        self.assertTrue(res["ok"], res["missing"])


class TestTable(unittest.TestCase):
    def test_md_cell_escapes_pipe_and_newline(self):
        self.assertEqual(rbb_core.md_cell("a|b\nc"), "a\\|b c")
        self.assertEqual(rbb_core.md_cell(None), "-")

    def test_render_table_row_cells(self):
        rows = [{"full_name": "o/r", "stargazersCount": 1, "pushedAt": "2026-10-01T00:00:00Z",
                 "language": "Python", "_lic_label": "mit", "openIssuesCount": 2,
                 "matched_keyword": ["kw"], "url": "https://github.com/o/r", "_activity": 2}]
        out = rbb_core.render_table(rows)
        lines = out.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(len(re.findall(r"(?<!\\)\|", lines[2])), 9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
