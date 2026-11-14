"""v0.3.8 安全与包装回归；全部离线运行。"""

from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import tomllib
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rbb_core = load_module("rbb_core", os.path.join(ROOT, "rbb_core.py"))
repo_scan = load_module(
    "rbb_repo_scan",
    os.path.join(ROOT, "skills", "reuse-before-build", "scripts", "repo_scan.py"),
)


class TestSharedRedaction(unittest.TestCase):
    def test_core_and_cli_redact_the_same_credentials(self):
        cases = {
            "https://alice:url-password@example.com/owner/repo.git": ("alice", "url-password"),
            "Cookie: session= cookie-secret\nnext line": ("cookie-secret",),
            "Authorization: Bearer header-secret-123": ("header-secret-123",),
            "access_token= token-secret-123": ("token-secret-123",),
        }
        for text, secrets in cases.items():
            with self.subTest(text=text):
                core = rbb_core.redact_text(text)
                cli = repo_scan.redact_text(text)
                self.assertEqual(core, cli)
                self.assertIn("[redacted]", core)
                for secret in secrets:
                    self.assertNotIn(secret, core)

    def test_only_the_shared_module_owns_redaction_regexes(self):
        for module in (rbb_core, repo_scan):
            for name in ("_SECRET_RX", "_URL_CRED_RX", "_HEADER_RX"):
                self.assertFalse(hasattr(module, name), f"{module.__name__} 又维护了 {name}")
        self.assertEqual(
            os.path.realpath(rbb_core.REDACTION_SCRIPT),
            os.path.realpath(os.path.join(os.path.dirname(repo_scan.__file__), "redaction.py")),
        )


class TestGhExecutableResolution(unittest.TestCase):
    def test_missing_path_result_never_falls_back_to_bare_gh(self):
        with mock.patch.object(rbb_core, "which_path_only", return_value=""):
            self.assertEqual(rbb_core.command_path("gh"), "")
            self.assertEqual(rbb_core.command_path("gh.exe"), "")
            with mock.patch.object(rbb_core, "_gh") as gh:
                with self.assertRaisesRegex(RuntimeError, "gh_binary"):
                    rbb_core.gh_json("gh", ["search", "repos"], 5)
                gh.assert_not_called()
            with mock.patch.object(rbb_core, "_gh") as gh:
                ok, _ = rbb_core.auth_ok("gh", 5)
                self.assertFalse(ok)
                gh.assert_not_called()

    def test_resolved_absolute_path_still_reaches_gh(self):
        payload = subprocess.CompletedProcess(["gh"], 0, "[]", "")
        with mock.patch.object(rbb_core, "which_path_only", return_value="C:/Tools/gh.exe"):
            with mock.patch.object(rbb_core, "_gh", return_value=payload) as gh:
                self.assertEqual(rbb_core.gh_json("gh", ["search", "repos"], 5), [])
                gh.assert_called_once_with("C:/Tools/gh.exe", ["search", "repos"], 5)


class TestSingleVersions(unittest.TestCase):
    def test_localized_verifier_has_one_version_assignment(self):
        path = os.path.join(ROOT, "skills", "reuse-before-build", "scripts", "verify_localized.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        versions = [
            node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
            and isinstance(node.value, ast.Constant)
        ]
        self.assertEqual(versions, ["1.2.7"])

    def test_plugin_and_project_versions_match(self):
        with open(os.path.join(ROOT, "plugin.toml"), "rb") as fh:
            plugin = tomllib.load(fh)
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
            project = tomllib.load(fh)
        self.assertEqual(plugin["plugin"]["version"], project["project"]["version"])


if __name__ == "__main__":
    unittest.main()
