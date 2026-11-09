"""在 SDK 桩上把插件类真跑一遍：导入、启动、四个工具的返回形态。

这是一条**结构**测试：证明入口签名、装饰器参数、返回值形态不会当场炸。
真机行为（权限、UI、10 秒就绪）仍需在 N.E.K.O 里验。
"""

import asyncio
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUB = os.path.join(ROOT, "tests", "stub")
for p in (STUB, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)



class _Config:
    def __init__(self, settings):
        self._settings = settings

    async def dump(self):
        return {"settings": dict(self._settings)}


class _Ctx:
    def __init__(self, settings=None):
        self.config = _Config(settings or {})


def _load_plugin_module():
    spec = importlib.util.spec_from_file_location(
        "rbb_plugin_under_test", os.path.join(ROOT, "__init__.py"),
        submodule_search_locations=[ROOT],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestPluginRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_plugin_module()

    def _run(self, coro):
        return asyncio.run(coro)

    async def _lifecycle(self, plugin, settings):
        plugin = plugin(_Ctx(settings))
        res = await plugin.on_startup()
        await asyncio.sleep(0.6)  # 让后台哈希探测跑完，避免 loop 关闭时被取消
        return plugin, res

    def test_startup_returns_ok(self):
        plugin, res = asyncio.run(self._lifecycle(self.mod.ReuseBeforeBuildPlugin, {}))
        self.assertTrue(res.ok, res.value)
        self.assertEqual(res.value["status"], "ready")

    def test_settings_are_read(self):
        plugin, _ = asyncio.run(self._lifecycle(
            self.mod.ReuseBeforeBuildPlugin,
            {"enable_github_scan": False, "default_stars": 120, "default_limit": 3, "updated_days": 90},
        ))
        self.assertFalse(plugin._enable_scan)
        self.assertEqual(plugin._stars, 120)
        self.assertEqual(plugin._limit, 3)
        self.assertEqual(plugin._updated_days, 90)

    def test_rules_tool_returns_five_rules(self):
        plugin, _ = asyncio.run(self._lifecycle(self.mod.ReuseBeforeBuildPlugin, {}))
        res = asyncio.run(plugin.rbb_rules())
        self.assertTrue(res.ok)
        rules = res.value["rules"]
        numbered = [line for line in rules.splitlines() if line[:2] in ("1.", "2.", "3.", "4.", "5.")]
        self.assertEqual(len(numbered), 5)
        self.assertEqual(res.value["verdict_order"], ["Join", "Extend", "Fork", "Build"])

    def test_scan_respects_disabled_switch(self):
        plugin, _ = asyncio.run(self._lifecycle(
            self.mod.ReuseBeforeBuildPlugin, {"enable_github_scan": False}))
        res = asyncio.run(plugin.rbb_scan(keywords=["anything"]))
        self.assertFalse(res.ok)
        self.assertIn("enable_github_scan", str(res.value))

    def test_scan_reports_unavailable_not_empty(self):
        plugin, _ = asyncio.run(self._lifecycle(
            self.mod.ReuseBeforeBuildPlugin, {"gh_binary": "gh-not-installed-xyz"}))
        res = asyncio.run(plugin.rbb_scan(keywords=["video clip"]))
        self.assertFalse(res.ok)
        msg = str(res.value)
        self.assertIn("code=2", msg)
        self.assertIn("不等于没有同类项目", msg)

    def test_check_tool_flags_missing_parts(self):
        plugin, _ = asyncio.run(self._lifecycle(self.mod.ReuseBeforeBuildPlugin, {}))
        res = asyncio.run(plugin.rbb_check(deliverable="## 找到了什么\n没有别的段落"))
        self.assertTrue(res.ok)
        self.assertFalse(res.value["ok"])
        self.assertIn("section:why", res.value["missing"])

    def test_verify_tool_passes_bundled_copy(self):
        plugin, _ = asyncio.run(self._lifecycle(self.mod.ReuseBeforeBuildPlugin, {}))
        res = asyncio.run(plugin.rbb_verify(path=os.path.join(
            ROOT, "skills", "reuse-before-build", "SKILL.md")))
        self.assertTrue(res.ok)
        self.assertEqual(res.value["exit_code"], 0)
        self.assertIn("verbatim", res.value["output"])

    def test_status_tool_has_hash(self):
        plugin, _ = asyncio.run(self._lifecycle(self.mod.ReuseBeforeBuildPlugin, {}))
        res = asyncio.run(plugin.rbb_status())
        self.assertTrue(res.ok)
        self.assertEqual(len(res.value["rule_sha256"]), 64)
        self.assertEqual(res.value["bundled_skill_verify"]["exit_code"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
