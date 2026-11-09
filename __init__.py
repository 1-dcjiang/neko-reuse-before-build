"""先搜后建（reuse_before_build）—— 把 reuse-before-build 规则装进 N.E.K.O。

设计（对齐 neko-plugin-dev 4.1.0）：
  · 入口只做「取参 → 调深函数 → Ok/Err」，业务在 rbb_core（宿主无关、可离线测试）
  · 描述写全「何时用 / 前置 / 限制 / ⚠️」，错误消息给下一步动作
  · 启动 10 秒内返回 ready；检索与校验都是按需触发，不占启动时间
  · 只读能力：不写文件、不提 PR、不发评论；公开写必须由用户逐次确认（见规则本体）
  · 参数做标量防御：宿主传参形态不稳，统一走 _params()
"""

from __future__ import annotations

import asyncio
import logging

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    ui,
)

from . import rbb_core

_logger = logging.getLogger("N.E.K.O.Plugin_reuse_before_build.plugin.reuse_before_build")

_RULES_PREAMBLE = (
    "以下是本插件随包冻结的规则正典（与上游 reuse-before-build 逐字一致）。"
    "它是**规则**不是建议：命中后按它执行，不要自行删条、合并或改编号。"
)


@neko_plugin
class ReuseBeforeBuildPlugin(NekoPluginBase):
    """写新程序之前先搜同类公开实现，优先贡献上游而不是重造。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._enable_scan: bool = True
        self._gh_binary: str = "gh"
        self._stars: int = 50
        self._limit: int = 6
        self._updated_days: int = 365
        self._timeout: int = 30
        self._python: str = ""
        self._rule_hash: str = ""

    # ══════════ 生命周期 ══════════

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        try:
            await self._load_settings()
            # 哈希计算要跑一次子进程，放后台，避免超过 10 秒就绪窗口
            asyncio.create_task(self._probe_hash())
            return Ok({"status": "ready", "gh_scan": self._enable_scan})
        except Exception as e:
            self.logger.exception("startup failed")
            return Err(SdkError(f"插件启动失败：{e}"))

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        return Ok({"status": "shutdown"})

    @lifecycle(id="reload")
    async def on_reload(self, **_):
        await self._load_settings()
        return Ok({"status": "reloaded"})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        await self._load_settings()
        return Ok({"status": "config_updated"})

    async def _load_settings(self):
        cfg = await self.config.dump()
        settings = cfg.get("settings", {}) if isinstance(cfg, dict) else {}
        self._enable_scan = bool(settings.get("enable_github_scan", True))
        self._gh_binary = str(settings.get("gh_binary") or "gh").strip() or "gh"
        self._stars = max(0, int(settings.get("default_stars", 50) or 50))
        self._limit = max(1, int(settings.get("default_limit", 6) or 6))
        self._updated_days = max(1, int(settings.get("updated_days", 365) or 365))
        self._timeout = max(5, int(settings.get("scan_timeout", 30) or 30))

    async def _probe_hash(self):
        try:
            h, err = await asyncio.to_thread(rbb_core.canonical_hash, self._python)
            self._rule_hash = h
            if err:
                self.logger.warning("规则哈希计算失败：%s", err)
        except Exception as e:
            self.logger.warning("规则哈希计算异常：%s", e)

    @staticmethod
    def _params(extra) -> dict:
        """宿主调用 llm_tool 时 parameters 键偶发被传成标量，这里统一防御。"""
        raw = extra.get("parameters") if isinstance(extra, dict) else None
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _as_keywords(value, params: dict) -> list[str]:
        raw = value if value else params.get("keywords")
        if isinstance(raw, (list, tuple)):
            items = [str(x) for x in raw]
        else:
            items = str(raw or "").replace("，", ",").split(",")
        return [k.strip() for k in items if k and k.strip()]

    # ══════════ AI 入口 ══════════

    @llm_tool(
        name="rbb_rules",
        description=(
            "取回「先搜后建」的规则正典与四步流程。何时用：用户要求写任何新程序/脚本/库/插件/服务，"
            "或说「帮我做一个 X」「从零实现」「从零写一个」时，先调这个再动手；不确定要不要走完整流程时也先调它。"
            "前置：无。返回：核心规则（不可变块，逐字）、两档位、四步流程、结论顺序 Join→Extend→Fork→Build。"
            "限制：只返回文本，不联网、不写文件。⚠️ 规则是约束不是建议，不要删条或改编号。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "rules=只要核心规则（默认，最省上下文）；flow=只要四步流程；full=整份 SKILL.md",
                    "enum": ["rules", "flow", "full"],
                }
            },
            "required": [],
        },
    )
    async def rbb_rules(self, section: str = "", **_):
        params = self._params(_)
        section = (section or params.get("section") or "rules").strip().lower()
        try:
            text = await asyncio.to_thread(rbb_core.skill_text, "SKILL.md")
        except Exception as e:
            return Err(SdkError(f"读取随包规则失败：{e}。重装插件或检查安装目录权限。"))
        if section == "full":
            return Ok({"section": "full", "content": text, "note": "整份 SKILL.md，篇幅较大。"})
        if section == "flow":
            flow = [
                "1. Discovery：三轮检索（GitHub 仓库/代码 → 包管理器与插件市场 → 候选仓库的 issue 里找人提过的同类需求）",
                "2. Triage：3–6 个候选，六维 0–2 打分（活跃度/维护者响应/许可/贡献友好度/需求匹配/工程质量）",
                "3. Contribute：先读 CONTRIBUTING 与最近合并的 PR；功能改动先开 issue，bug 可直接 PR",
                "4. Deliver：四段交付（找到了什么 / 为什么选它 / 我要加什么 / 备选与风险）",
            ]
            return Ok({"section": "flow", "flow": flow,
                       "verdict_order": ["Join", "Extend", "Fork", "Build"],
                       "note": "完整档走四步；一次性小脚本走快速档，回复固定三行：复用 / 改动 / 风险。"})
        return Ok({
            "section": "rules",
            "rules": rbb_core.core_rules_block(),
            "verdict_order": ["Join", "Extend", "Fork", "Build"],
            "preamble": _RULES_PREAMBLE,
            "canonical_sha256": self._rule_hash or "(正在计算，稍后调 rbb_status 查看)",
        })

    @llm_tool(
        name="rbb_scan",
        description=(
            "用本机 gh CLI 检索 GitHub 上的同类仓库，返回去重后的候选表（★/最近推送/语言/许可/open issues/URL）。"
            "何时用：进入 Discovery 阶段、或需要证明「有没有现成的」时。前置：本机装了 GitHub CLI 且已登录。"
            "限制：只读搜索，不提 PR、不发 issue、不改任何远端状态；最多 5 个关键词，单次每个关键词最多 10 条。"
            "⚠️ 返回 code=2/3 表示「没查成」，绝不等于「不存在」：此时必须换检索串或让用户开联网，不要断言没有同类项目。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "1–5 个检索串，英文效果最好（如 \"obsidian sync plugin\"、\"video auto clip highlight\"）",
                },
                "stars": {"type": "integer", "description": "星数下限，默认取插件设置（50）"},
                "limit": {"type": "integer", "description": "每个关键词返回条数，1–10，默认取插件设置（6）"},
                "updated_since": {"type": "string", "description": "最近推送日期下限 YYYY-MM-DD，默认一年前"},
            },
            "required": ["keywords"],
        },
    )
    async def rbb_scan(self, keywords=None, stars: int = 0, limit: int = 0,
                       updated_since: str = "", **_):
        params = self._params(_)
        if not self._enable_scan:
            return Err(SdkError(
                "GitHub 检索已被关闭（设置项 enable_github_scan=false）。"
                "要检索请让用户在插件设置里打开；不打开时改用网页检索串人工复核，"
                "但不得因此断言「没有同类项目」。"
            ))
        kws = self._as_keywords(keywords, params)
        if not kws:
            return Err(SdkError("缺少检索关键词 keywords。给 1–5 个英文检索串再调一次。"))
        if len(kws) > 5:
            return Err(SdkError(f"关键词给了 {len(kws)} 个，上限 5 个。合并同义词后再试。"))
        st = int(stars or params.get("stars") or self._stars)
        lm = int(limit or params.get("limit") or self._limit)
        lm = min(10, max(1, lm))
        since = str(updated_since or params.get("updated_since") or "").strip()

        res = await asyncio.to_thread(
            rbb_core.search_candidates,
            kws, st, lm, since, self._gh_binary, self._timeout, self._updated_days,
        )
        payload = {
            "code": res["code"],
            "ok": res["ok"],
            "keywords": res["keywords"],
            "stars": res.get("stars", st),
            "updated_since": res.get("updated_since", since),
            "table": res["table"],
            "count": len(res["rows"]),
            "candidates": [
                {
                    "repo": r.get("full_name"),
                    "stars": r.get("stargazersCount"),
                    "pushed_at": (r.get("pushedAt") or "")[:10],
                    "language": r.get("language"),
                    "license": r.get("_lic_label"),
                    "open_issues": r.get("openIssuesCount"),
                    "url": r.get("url"),
                    "matched_keyword": r.get("matched_keyword"),
                }
                for r in res["rows"]
            ],
            "note": res["note"],
        }
        if res["code"] != 0:
            hint = res["hint"] or rbb_core.FAIL_HINT
            return Err(SdkError(
                f"检索失败（code={res['code']}）：{res['reason']}\n"
                f"{hint}\n"
                "把这句话原样转述给用户：检索失败不等于没有同类项目，"
                "不要据此断言不存在，也不要直接开写。"
            ))
        if not res["rows"]:
            payload["hint"] = rbb_core.EMPTY_HINT
            payload["must_say"] = "本轮检索成功但 0 命中；列出用过的检索串，并按提示换同义词再跑一轮。"
        return Ok(payload)

    @llm_tool(
        name="rbb_check",
        description=(
            "机械检查一段交付文本是否满足本 skill 的格式硬要求：四段（找到了什么/为什么选它/我要加什么/备选与风险）、"
            "结论词（Join/Extend/Fork/Build 之一）、以及判定 Build 时首行必须有 `不可复用原因 / Not reusable because:`。"
            "何时用：准备把候选结论回复给用户之前，把整段回复粘进来跑一次。前置：无。"
            "限制：只查结构，不判断内容真伪（仓库与星数仍需自己核）。⚠️ FAIL 时先补缺失项再回复，不要把 FAIL 稿直接交付。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "deliverable": {"type": "string", "description": "准备交付的完整回复文本（Markdown 原文）"},
            },
            "required": ["deliverable"],
        },
    )
    async def rbb_check(self, deliverable: str = "", **_):
        params = self._params(_)
        text = deliverable or params.get("deliverable") or ""
        if not str(text).strip():
            return Err(SdkError("缺少 deliverable（准备交付的回复正文）。把整段回复粘进来再调一次。"))
        res = rbb_core.check_deliverable(str(text))
        if not res["ok"]:
            return Ok({
                "ok": False,
                "missing": res["missing"],
                "notes": res["notes"],
                "fix": "补齐上方缺失项后重新调 rbb_check，直到 ok=true 再回复用户。",
            })
        return Ok({"ok": True, "missing": [], "notes": res["notes"]})

    @llm_tool(
        name="rbb_verify",
        description=(
            "校验一份「本地化副本」是否保住了不可变规则：不变块哈希（逐字）或规则条数 1..5 + 每条规则锚点（翻译版），"
            "外加声明行 `localized: host=… | kept=… | changed=… | unsupported=…`。"
            "何时用：把本 skill 改写成别的宿主格式（单文件系统提示、别的 frontmatter、别的语言）之后必须跑一次。"
            "前置：待校验文件存在且是绝对路径或相对插件目录可解析的路径。"
            "限制：只读校验，不改目标文件。⚠️ 退出码 0 是唯一通过标准；失败会列出缺了什么，按提示补回，不许静默删除规则。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "待校验的本地化副本路径（.md）"},
            },
            "required": ["path"],
        },
    )
    async def rbb_verify(self, path: str = "", **_):
        params = self._params(_)
        target = str(path or params.get("path") or "").strip()
        if not target:
            return Err(SdkError("缺少 path（待校验副本路径）。给绝对路径再调一次。"))
        res = await asyncio.to_thread(rbb_core.verify_localized, target, self._python)
        if res["rc"] == 0:
            return Ok({"ok": True, "exit_code": 0, "output": res["out"]})
        return Ok({
            "ok": False,
            "exit_code": res["rc"],
            "output": res["out"] or res["err"],
            "fix": ("rc=2 通常是路径不在包内却带了 --canonical，或文件不存在；"
                    "rc=1 是缺项，按 MISSING 列表补回后再跑，直到 rc=0。"),
        })

    @llm_tool(
        name="rbb_status",
        description=(
            "查看本插件的运行状态：规则正典哈希、随包技能是否完整、gh 是否可用、当前设置（星数门槛/条数/时间窗）。"
            "何时用：规则哈希显示「正在计算」、检索报 code=2、或想确认插件装好没坏时。前置：无。"
            "限制：只读，不安装依赖、不改配置。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
    )
    async def rbb_status(self, **_):
        if not self._rule_hash:
            await self._probe_hash()
        gh_ok, gh_why = await asyncio.to_thread(rbb_core.auth_ok, self._gh_binary, self._timeout)
        bundled = await asyncio.to_thread(rbb_core.verify_localized, rbb_core.SKILL_MD, self._python)
        return Ok({
            "plugin": "reuse_before_build",
            "rule_sha256": self._rule_hash or "无法计算",
            "bundled_skill_verify": {
                "exit_code": bundled["rc"],
                "output": bundled["out"] or bundled["err"],
            },
            "gh": {"available": gh_ok, "detail": gh_why or "ok", "binary": self._gh_binary},
            "settings": {
                "enable_github_scan": self._enable_scan,
                "default_stars": self._stars,
                "default_limit": self._limit,
                "updated_days": self._updated_days,
                "scan_timeout": self._timeout,
            },
        })

    # ══════════ UI ══════════

    @plugin_entry(
        id="self_check",
        name="自检规则完整性",
        description="确认随包规则本体未被改动，并检查本机 gh 是否可用。",
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    @ui.action(id="self_check", label="自检规则完整性", tone="primary", refresh_context=True)
    async def self_check(self, **_):
        """给用户一个可点的自检：确认随包规则没被改动、gh 能用。"""
        if not self._rule_hash:
            await self._probe_hash()
        bundled = await asyncio.to_thread(rbb_core.verify_localized, rbb_core.SKILL_MD, self._python)
        gh_ok, gh_why = await asyncio.to_thread(rbb_core.auth_ok, self._gh_binary, self._timeout)
        self.report_status({
            "status": "completed" if bundled["rc"] == 0 else "failed",
            "progress": 100,
            "message": "规则完整 · gh 可用" if (bundled["rc"] == 0 and gh_ok)
            else ("规则被改动" if bundled["rc"] != 0 else "gh 不可用"),
        })
        return Ok({
            "rule_sha256": self._rule_hash or "无法计算",
            "skill_exit_code": bundled["rc"],
            "detail": (bundled["out"] or bundled["err"])[:400],
            "gh_available": gh_ok,
            "gh_detail": gh_why or "ok",
        })
