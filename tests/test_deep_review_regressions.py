"""v0.3.7 补深审遗留项的离线回归；只操作临时副本。"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(ROOT, "skills", "reuse-before-build")
SKILL = os.path.join(SKILL_DIR, "SKILL.md")
ZH = os.path.join(SKILL_DIR, "references", "zh-guide.md")
VERIFY = os.path.join(SKILL_DIR, "scripts", "verify_localized.py")
DECL = "\nlocalized: host=test | kept=all | changed=packaging | unsupported=none\n"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load_module("rbb_core_deep_review", os.path.join(ROOT, "rbb_core.py"))
repo_scan = load_module(
    "rbb_repo_scan_deep_review", os.path.join(SKILL_DIR, "scripts", "repo_scan.py")
)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


FULL = """## What I found
| Repo | Date |
|---|---|
| owner/demo | 2026-10-01 |

## Why this one
The requested feature fits upstream.

## What I will add (issue / PR plan)
Add the feature and tests.
Modifiability: green (clean core and tests).

## Alternatives and risks
Extend if the maintainer rejects the feature.

Verdict: Join.
"""


class TestDeliverableContract(unittest.TestCase):
    def test_canonical_templates_pass_their_own_checker(self):
        for path, marker in ((SKILL, "## What I found"), (ZH, "## 找到了什么")):
            with self.subTest(path=path):
                blocks = re.findall(r"```markdown\n(.*?)```", read(path), re.S)
                template = next(block for block in blocks if marker in block)
                result = core.check_deliverable(template)
                self.assertTrue(result["ok"], result)

    def test_every_complete_worked_example_passes(self):
        blocks = re.findall(
            r"```markdown\n(.*?)```",
            read(os.path.join(SKILL_DIR, "references", "worked-examples.md")), re.S,
        )
        self.assertEqual(len(blocks), 4)
        for block in blocks:
            with self.subTest(verdict=core.check_deliverable(block)["verdict"]):
                result = core.check_deliverable(block)
                self.assertTrue(result["ok"], result)

    def test_quick_lane_is_three_nonempty_lines_in_either_language(self):
        samples = (
            "Reused: owner/demo (MIT) — fits\nChange: add a caller\nRisk: API mismatch",
            "复用：owner/demo（MIT）——合用\n改动：增加调用\n风险：接口不匹配",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                result = core.check_deliverable(sample)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["lane"], "quick")
        bad = samples[0].replace("add a caller", "")
        self.assertIn("quick:change_empty", core.check_deliverable(bad)["missing"])

    def test_build_gate_must_be_first_and_nonempty(self):
        build = FULL.replace("Verdict: Join.", "Verdict: Build.")
        late = build + "\nNot reusable because: requirement mismatch\n"
        self.assertIn("gate_line", core.check_deliverable(late)["missing"])
        empty = "不可复用原因：\n" + build
        self.assertIn("gate_reason", core.check_deliverable(empty)["missing"])
        valid = "不可复用原因：需求本质不同。\n" + build
        self.assertTrue(core.check_deliverable(valid)["ok"])

    def test_verdict_is_structured_and_red_cannot_join(self):
        red = FULL.replace("Modifiability: green", "Modifiability: red")
        result = core.check_deliverable(red)
        self.assertEqual(result["verdict"], "Join")
        self.assertEqual(result["modifiability"], "red")
        self.assertIn("join_requires_green", result["missing"])
        chinese = FULL.replace("Verdict: Join.", "结论：加入。")
        self.assertEqual(core.check_deliverable(chinese)["verdict"], "Join")


class TestRulesAndDocs(unittest.TestCase):
    def test_default_rule_payload_covers_every_hard_gate(self):
        rules = core.mandatory_rules()
        for needle in (
            "Assume it already exists", "Modifiability gate", "Hard constraints",
            "Untrusted repository content", "Branch first", "thin adapter", "per-action confirmation",
        ):
            self.assertIn(needle, rules)
        steps = core.flow_steps()
        self.assertEqual([step["title"] for step in steps], [
            "Step 1 — Discover", "Step 2 — Evaluate", "Step 3 — Contribute", "Step 4 — Deliver",
        ])
        self.assertIn("Modifiability gate", steps[1]["content"])
        self.assertIn("Branch first", steps[2]["content"])

    def test_chinese_and_english_verdict_tables_agree(self):
        english, chinese = read(SKILL), read(ZH)
        for verdict in ("Join", "Extend", "Fork", "Build"):
            self.assertEqual(len(re.findall(rf"^\| \*\*{verdict}", english, re.M)), 1)
            self.assertEqual(len(re.findall(rf"^\| \*\*{verdict}", chinese, re.M)), 1)
        self.assertIn("modifiability green only", english)
        self.assertIn("可改性仅限绿", chinese)
        self.assertNotIn("可改性为绿或黄", chinese)
        self.assertIn("没有第五个判决", chinese)
        self.assertIn("数据，不是指令", chinese)

    def test_paste_prompts_include_current_hard_constraints(self):
        text = read(os.path.join(SKILL_DIR, "references", "host-adapters.md"))
        for needle in (
            "modifiability", "Never commit or push directly to main/master",
            "thin adapter", "data, not instructions", "per-action user confirmation",
            "可改性仅限绿", "绝不直写 main/master", "薄适配层", "数据，不是指令", "逐次明确确认",
        ):
            self.assertIn(needle, text)

    def test_core_and_cli_exit_codes_are_not_conflated(self):
        self.assertIn("两套独立契约", core.__doc__)
        self.assertIn("CLI：0 成功 · 2 用法错误", core.__doc__)

    def test_market_metadata_has_no_changelog_or_broad_trigger_keywords(self):
        with open(os.path.join(ROOT, "plugin.toml"), "rb") as handle:
            plugin = tomllib.load(handle)["plugin"]
        self.assertNotRegex(plugin["short_description"], r"v0\.\d+\.\d+")
        self.assertTrue({"pull request", "scaffold", "contribute"}.isdisjoint(plugin["keywords"]))


class TestLocalizedVerifier(unittest.TestCase):
    def verify_text(self, source, mutate=lambda text: text, declaration=DECL):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "localized.md")
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(mutate(read(source)) + declaration)
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", VERIFY, target],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
        return proc

    def test_new_required_anchors_cannot_be_deleted(self):
        cases = {
            "untrusted_repository_content": "data, not instructions",
            "modifiability_green_only": "modifiability green only",
            "no_fifth_verdict": "There is no fifth verdict",
            "branch_first": "Branch first, never the default branch",
            "thin_adapter": "stable seam and a thin adapter",
            "never_join_red": "Never Join a repo whose modifiability",
        }
        with open(os.path.join(SKILL_DIR, "localization.contract.json"), encoding="utf-8") as handle:
            contract = json.load(handle)
        self.assertGreaterEqual(len(contract["must_have"]), 12)
        for anchor, needle in cases.items():
            with self.subTest(anchor=anchor):
                proc = self.verify_text(SKILL, lambda text: text.replace(needle, "removed"))
                self.assertEqual(proc.returncode, 1, proc.stdout)
                self.assertIn(f"anchor:{anchor}", proc.stdout)

    def test_chinese_untrusted_section_is_also_required(self):
        proc = self.verify_text(ZH, lambda text: text.replace("数据，不是指令", "资料"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("anchor:untrusted_repository_content", proc.stdout)

    def test_bundled_english_skill_cannot_masquerade_as_translated(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = os.path.join(tmp, "skill")
            shutil.copytree(SKILL_DIR, copied)
            target = os.path.join(copied, "SKILL.md")
            text = read(target).replace(
                "<!-- RBB:INVARIANTS:BEGIN -->",
                "<!-- RBB:INVARIANTS:BEGIN -->\n<!-- RBB:INVARIANTS:LANG: en -->", 1,
            )
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", os.path.join(copied, "scripts", "verify_localized.py"), target],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("canonical_invariants_hash", proc.stdout)

    def test_tilde_fences_hide_markers_and_declarations(self):
        demo = (
            "~~~markdown\n<!-- RBB:INVARIANTS:BEGIN -->\n1. fake\n"
            "<!-- RBB:INVARIANTS:END -->\n~~~\n"
        )
        proc = self.verify_text(SKILL, lambda text: demo + text)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        declaration = "\n~~~text\nlocalized: host=test | kept=all | changed=none | unsupported=none\n~~~\n"
        proc = self.verify_text(SKILL, declaration=declaration)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("declaration", proc.stdout)

    def test_import_does_not_reconfigure_host_stdout(self):
        code = (
            "import importlib.util, io, sys; original = sys.stdout; "
            "sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding='ascii'); "
            f"spec = importlib.util.spec_from_file_location('verify_import', {VERIFY!r}); "
            "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
            "encoding = sys.stdout.encoding; sys.stdout = original; print(encoding)"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip().lower(), "ascii")


class TestProcessHardening(unittest.TestCase):
    def test_relative_windows_which_result_is_rejected_by_core_and_cli(self):
        for module in (core, repo_scan):
            with self.subTest(module=module.__name__):
                with mock.patch.dict(os.environ, {"PATH": os.path.dirname(sys.executable)}):
                    with mock.patch.object(module.shutil, "which", return_value=".\\gh.exe"):
                        self.assertEqual(module.which_path_only("gh"), "")

    def test_core_retries_rate_limit_once_within_budget(self):
        rows = [{"url": "https://github.com/owner/demo", "name": "demo", "owner": {"login": "owner"}}]
        with mock.patch.object(core, "gh_json", side_effect=[RuntimeError("HTTP 429 rate limit"), rows]) as call:
            with mock.patch.object(core.time, "sleep") as sleep:
                got, error = core._scan_one("demo", "gh", 0, "2026-01-01", 3, 10)
        self.assertEqual(error, "")
        self.assertEqual(got[0]["full_name"], "owner/demo")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args_list[1].args[2], 8)
        sleep.assert_called_once_with(2)

    def test_gh_subprocesses_request_no_console_window(self):
        with mock.patch.object(core.subprocess, "run") as run:
            core._gh("gh", ["auth", "status"], 1)
        self.assertEqual(run.call_args.kwargs["creationflags"], core.GH_CREATIONFLAGS)
        completed = subprocess.CompletedProcess(["gh"], 0, "[]", "")
        with mock.patch.object(repo_scan.subprocess, "run", return_value=completed) as run:
            repo_scan.gh_json(["search"], gh_binary="gh")
        self.assertEqual(run.call_args.kwargs["creationflags"], repo_scan.GH_CREATIONFLAGS)

    def test_missing_python_never_falls_back_to_bare_name(self):
        with mock.patch.object(core.sys, "executable", ""):
            with mock.patch.object(core, "which_path_only", return_value=""):
                self.assertEqual(core.resolve_python(), "")


if __name__ == "__main__":
    unittest.main()
