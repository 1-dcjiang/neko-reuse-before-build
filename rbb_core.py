"""reuse-before-build · 宿主无关的纯逻辑层（N.E.K.O 适配版）。

这一层不 import 任何 N.E.K.O SDK，只用标准库，因此可以在没有宿主的机器上直接跑测试。
它做四件事：
  1. search_candidates()  用 gh CLI 检索 GitHub 候选仓库，并把「检索失败」与「成功但 0 命中」分开；
  2. check_deliverable()  机械检查一段交付文本是否含四段格式、结论词与不可复用原因门禁行；
  3. verify_localized()   调用随包校验器检查本地化副本是否保住了不可变块；
  4. skill_text() / canonical_hash()  读取随包技能本体与正典哈希。

退出码语义沿用 CLI（scripts/repo_scan.py）：
  0 检索成功（可能 0 命中）· 2 gh 不可用（未安装/未登录/主机不通/参数非法）
  3 gh 可用但全部关键词检索失败 · 4 预算耗尽、检索未完成
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from typing import Any, Callable

_logger = logging.getLogger(__name__)

PERMISSIVE = {"mit", "apache-2.0", "bsd-3-clause", "bsd-2-clause", "isc", "mpl-2.0", "0bsd", "unlicense"}

AUTH_HINT = (
    "gh 无法确认可用（可能是未登录、无权限，也可能是网络/主机配置不通）。"
    "先执行 `gh auth status` 查看，必要时 `gh auth login`；"
    "完全没有 gh 时改用 references/discovery-playbook.md 的网页检索串。"
)
FAIL_HINT = (
    "检索失败 ≠ 没有同类项目。检查 `gh auth status` 与网络后重跑，"
    "或改用 references/discovery-playbook.md 的网页检索串手工复核；不要据此断言不存在。"
)
EMPTY_HINT = (
    "检索成功，但 0 命中。0 命中不等于不存在：换英文同义词、降 stars、放宽时间窗再跑一轮，"
    "或用 references/discovery-playbook.md 的生态检索补查。"
)
BUDGET_HINT = (
    "检索预算用完了，部分关键词没有真正查。不要据此断言「没有同类项目」；"
    "增大 scan_timeout / 工具 timeout，或减少关键词后重跑。"
)

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.join(HERE, "skills", "reuse-before-build")
VERIFY_SCRIPT = os.path.join(SKILL_DIR, "scripts", "verify_localized.py")
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")
PLUGIN_TOML = os.path.join(HERE, "plugin.toml")

# gh 只允许这两个文件名：配置项和显式调用都不能指向 .bat/.cmd 或任意程序。
GH_NAMES = {"gh", "gh.exe"}
_SECRET_RX = re.compile(
    r"(?i)(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"bearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"(?:access[_-]?token|api[_-]?key|authorization|token|password|secret)\s*[:=]\s*\S+)"
)


def redact_text(value: object, limit: int = 300) -> str:
    """错误文本回给模型前脱敏：不把 token、口令和本机绝对路径原样吐出去。"""
    text = str(value or "")
    home = os.path.expanduser("~")
    if home:
        text = text.replace(home, "~")
    text = _SECRET_RX.sub("[redacted]", text)
    return text[:limit]


def normalize_gh_binary(value: object) -> str:
    """配置项只接受 `gh` / `gh.exe`；任何自定义路径都回退到 PATH 里的 gh。"""
    raw = str(value or "").strip()
    if raw.lower() in GH_NAMES:
        return raw.lower()
    return "gh"


def resolve_gh_executable(value: object) -> str:
    """只接受字面量 gh / gh.exe；.bat/.cmd 会经 cmd.exe 解释参数，直接拒绝。

    返回空串表示值非法，调用方把它当成「gh 不可用」，而不是悄悄换成另一个程序。
    """
    raw = str(value or "").strip()
    if raw.lower() in GH_NAMES:
        return raw.lower()
    return ""


def which_path_only(name: str) -> str:
    """只从 PATH 解析可执行文件，过滤空条目与当前目录。

    Windows 的 `shutil.which()` 会把当前目录补进搜索路径；在刚 clone 的第三方仓库
    里运行插件时，目录里的 `gh.exe` 会被优先执行。这里先剔除当前目录，再交给
    `shutil.which(path=...)`，返回绝对路径。
    """
    raw = os.environ.get("PATH", "")
    cwd = os.path.normcase(os.path.abspath(os.curdir))
    entries = [
        part for part in raw.split(os.pathsep)
        if part and os.path.normcase(os.path.abspath(part)) != cwd
    ]
    if not entries:
        return ""
    return shutil.which(name, path=os.pathsep.join(entries)) or ""


def resolve_gh_path(value: object) -> str:
    """白名单校验 + 只在 PATH 里解析成绝对路径；空串表示不可用。"""
    name = resolve_gh_executable(value)
    if not name:
        return ""
    return which_path_only(name)


def command_path(value: object) -> str:
    """给 gh_json/auth_ok 用：只接受字面量 gh/gh.exe，或由同一 PATH 解析出的绝对路径。

    `search_candidates()` 为了避免当前目录的 `gh.exe` 劫持，会先调用
    `resolve_gh_path()` 拿到绝对路径，再把它传给 `auth_ok()` / `_scan_one()`。
    因此这里不能只认字面量：绝对路径必须与当前 PATH 解析结果指向同一个文件，
    否则仍视为非法。这样既不会把自家解析结果拦掉，也不会放行任意路径。
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        base = os.path.basename(raw).lower()
        if base not in GH_NAMES:
            return ""
        resolved = which_path_only(base)
        if not resolved:
            return ""
        if os.path.normcase(os.path.realpath(resolved)) != os.path.normcase(os.path.realpath(raw)):
            return ""
        return resolved
    name = resolve_gh_executable(raw)
    return which_path_only(name) or name


KEYWORD_MAX = 200
_QUALIFIER_RX = re.compile(
    r"(?i)(?:^|\s)(?:stars|archived|pushed|created|updated|is|topic|language|license|"
    r"owner|repo|user|org|size|fork|mirror|template):"
)


def validate_keyword(value: object) -> str:
    """关键词必须是纯文本：拒绝空、NUL/控制字符、超长与能覆盖过滤条件的限定符。"""
    text = str(value or "").strip()
    if not text:
        raise ValueError("关键词不能为空")
    if len(text) > KEYWORD_MAX:
        raise ValueError(f"单个关键词不能超过 {KEYWORD_MAX} 字符")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError("关键词里不能有 NUL 或其它控制字符")
    if _QUALIFIER_RX.search(text):
        raise ValueError("关键词里不能带 stars:/pushed:/archived:/user:/org: 等检索限定符")
    return text


def normalize_keywords(keywords: object) -> tuple[list[str], str]:
    """返回 (合法关键词, 错误)。错误非空时调用方按 code=2 处理，不发检索。"""
    if isinstance(keywords, (list, tuple)):
        items = list(keywords)
    else:
        items = str(keywords or "").replace("，", ",").split(",")
    clean: list[str] = []
    for item in items:
        try:
            kw = validate_keyword(item)
        except ValueError as exc:
            return [], str(exc)
        if kw not in clean:
            clean.append(kw)
    return clean, ""

# 紧急降级值：不是「默认值」。真正的默认值只在 plugin.toml 的 [settings] 写一份；
# 只有 plugin.toml 缺失/解析失败时才落到这里，并把 enable_github_scan 关掉、条数压到最小。
SETTINGS_DEFAULTS: dict[str, Any] = {
    "enable_github_scan": False,
    "gh_binary": "gh",
    "default_stars": 0,
    "default_limit": 1,
    "updated_days": 1,
    "scan_timeout": 5,
}


def load_manifest_settings(plugin_toml: str = "") -> tuple[dict[str, Any], str]:
    """读 plugin.toml 的 [settings]；返回 (配置, 警告)。失败时警告非空，不静默。"""
    path = plugin_toml or PLUGIN_TOML
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:
        warning = f"plugin.toml 读取/解析失败（{path}）：{exc}"
        _logger.warning("%s；回退代码内置兜底默认值", warning)
        return {}, warning
    section = data.get("settings")
    if not isinstance(section, dict):
        warning = f"plugin.toml 缺少 [settings] 表（{path}）"
        _logger.warning("%s；回退代码内置兜底默认值", warning)
        return {}, warning
    return section, ""


def manifest_settings(plugin_toml: str = "") -> dict[str, Any]:
    """读 plugin.toml 里的 [settings] 作为默认值；读不到就返回空表（不抛）。"""
    return load_manifest_settings(plugin_toml)[0]


def _as_bool(value: Any, default: bool) -> bool:
    """显式解析布尔值：TOML 布尔直接用，字符串 "false"/"0"/"off" 不许被 bool() 当成 True。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def resolve_settings(section: Any = None, *, plugin_toml: str = "") -> dict[str, Any]:
    """把「代码兜底 → plugin.toml 默认 → 运行时配置段」三层合并成可用的六个值。

    宿主侧 `self.config.dump()` 不可用时（例如在没有真实 ctx 的测试环境），
    调用方传 None 即可，这里仍然返回一套可运行的值，不让插件因为读不到配置就启动失败。
    `settings_source` / `settings_warning` 供 rbb_status 如实显示当前用的是哪一层。
    """
    merged: dict[str, Any] = dict(SETTINGS_DEFAULTS)
    manifest, warning = load_manifest_settings(plugin_toml)
    merged.update({k: v for k, v in manifest.items() if k in SETTINGS_DEFAULTS})
    if isinstance(section, dict):
        merged.update({k: v for k, v in section.items() if k in SETTINGS_DEFAULTS})

    def _int(key: str, lo: int, hi: int) -> int:
        try:
            value = int(merged.get(key))
        except (TypeError, ValueError):
            value = int(SETTINGS_DEFAULTS[key])
        return max(lo, min(hi, value))

    gh_binary = normalize_gh_binary(merged.get("gh_binary"))
    if str(merged.get("gh_binary") or "gh").strip().lower() not in GH_NAMES:
        warning = (warning + "；" if warning else "") + (
            "gh_binary 只允许 gh / gh.exe，已回退到 gh（不支持自定义可执行路径）"
        )
    source = "runtime" if isinstance(section, dict) and section else (
        "plugin.toml" if manifest else "emergency-defaults")
    return {
        "enable_github_scan": _as_bool(merged.get("enable_github_scan"),
                                       bool(SETTINGS_DEFAULTS["enable_github_scan"])),
        "gh_binary": gh_binary,
        "default_stars": _int("default_stars", 0, 10_000_000),
        "default_limit": _int("default_limit", 1, 50),
        "updated_days": _int("updated_days", 1, 3650),
        "scan_timeout": _int("scan_timeout", 5, 300),
        "settings_source": source,
        "settings_warning": warning,
    }



# ────────────────────────── 时间与格式化 ──────────────────────────

def default_since(days: int = 365, today: dt.date | None = None) -> str:
    """默认时间窗用天数回退，避免闰日 2/29 的 replace(year-1) 抛 ValueError。"""
    today = today or dt.date.today()
    return (today - dt.timedelta(days=max(1, int(days)))).isoformat()


def activity_points(pushed_at: object) -> int:
    if not isinstance(pushed_at, str) or not pushed_at:
        return 0
    try:
        pushed = dt.datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
        days = (dt.datetime.now(dt.timezone.utc) - pushed).days
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 0
    if days <= 90:
        return 2
    if days <= 365:
        return 1
    return 0


def license_flags(license_obj: dict | None) -> tuple[int, str]:
    key = ((license_obj or {}).get("key") or "").lower()
    if not key:
        return 0, "无 LICENSE ⚠"
    if key in PERMISSIVE:
        return 2, (license_obj or {}).get("spdxId") or key
    return 1, (license_obj or {}).get("spdxId") or key


def md_cell(value: object) -> str:
    """表格单元格转义：竖线会切断 Markdown 表格，换行会切断行。"""
    if value is None:
        return "-"
    text = str(value)
    if text == "":
        return "-"
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_table(rows: list[dict]) -> str:
    header = "| 仓库 | ★ | 最近推送 | 语言 | 许可 | open issues | 命中关键词 | URL |"
    sep = "|---|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for row in rows:
        pushed = (row.get("pushedAt") or "")[:10] or "-"
        if row.get("_activity", 1) == 0:
            pushed = f"{pushed} ⚠停滞"
        lines.append(
            "| {name} | {stars} | {pushed} | {lang} | {lic} | {issues} | {kw} | {url} |".format(
                name=md_cell(row.get("full_name")),
                stars=md_cell(row.get("stargazersCount") or 0),
                pushed=md_cell(pushed),
                lang=md_cell(row.get("language")),
                lic=md_cell(row.get("_lic_label")),
                issues=md_cell(row.get("openIssuesCount")),
                kw=md_cell(", ".join(row.get("matched_keyword") or [])),
                url=md_cell(row.get("url")),
            )
        )
    return "\n".join(lines)


# ────────────────────────── gh 调用 ──────────────────────────

def _gh(gh_binary: str, args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        [gh_binary, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
    )


def parse_search_output(out: str) -> list[dict]:
    """把 gh search 的 JSON 输出解析成行；契约测试直接用录好的真实输出。

    只保留 dict 行：gh 偶尔会输出标量或嵌套错误对象，后面的 `row.get` 不能因此崩。
    """
    text = (out or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise RuntimeError(f"gh 输出不是 JSON（{exc}）：{redact_text(text, 200)}") from None
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def gh_json(gh_binary: str, args: list[str], timeout: float = 30) -> list[dict]:
    exe = command_path(gh_binary)
    if not exe:
        raise RuntimeError("gh_binary 非法：只允许 gh / gh.exe")
    try:
        proc = _gh(exe, args, timeout)
    except FileNotFoundError:
        raise RuntimeError("gh 可执行文件不在了（PATH 变了？）") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gh 超时（>{timeout:g}s）") from None
    except ValueError as exc:
        raise RuntimeError(f"gh 参数非法：{redact_text(exc)}") from None
    except OSError as exc:
        raise RuntimeError(f"gh 启动失败：{redact_text(exc)}") from None
    if proc.returncode != 0:
        raise RuntimeError(f"gh 失败（exit {proc.returncode}）：{redact_text(proc.stderr.strip())}")
    return parse_search_output(proc.stdout)


def auth_ok(gh_binary: str = "gh", timeout: float = 30) -> tuple[bool, str]:
    """只查检索要用的那一个 host：`gh auth status` 在任一已配置 host 掉线时会整体非零。"""
    exe = command_path(gh_binary)
    if not exe:
        return False, "gh_binary 非法：只允许 gh / gh.exe"
    host = os.environ.get("GH_HOST") or "github.com"
    try:
        proc = _gh(exe, ["auth", "status", "-h", host], timeout)
    except FileNotFoundError:
        return False, "gh 可执行文件不在了（PATH 变了？）"
    except subprocess.TimeoutExpired:
        return False, f"`gh auth status` 超时（>{timeout:g}s）"
    except ValueError as exc:
        return False, f"gh 参数非法：{redact_text(exc, 200)}"
    except OSError as exc:
        return False, f"gh 启动失败：{redact_text(exc, 200)}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, f"{host}：{redact_text(detail[0], 200) if detail else 'gh auth status 返回非零'}"
    return True, ""


def search_args(keyword: str, stars: int, updated_since: str, limit: int) -> list[str]:
    """构造 gh search argv：所有选项放在 `--` 之前，检索串放在 `--` 之后。

    关键词以 `-` 开头时（例如 `-foo`）不加 `--` 会被 gh 当成选项，直接报错或误解析。
    """
    query = f"{keyword} stars:>={stars} pushed:>={updated_since}"
    fields = "url,name,owner,stargazersCount,pushedAt,license,isArchived,language,openIssuesCount,description"
    return ["search", "repos", "--archived=false", "--limit", str(limit), "--json", fields, "--", query]


def normalize_search_row(row: dict, keyword: str) -> dict:
    """补 full_name / matched_keyword，不改 gh 原字段。"""
    owner = (row.get("owner") or {}).get("login", "")
    row["full_name"] = f"{owner}/{row.get('name', '')}" if owner else row.get("name", "")
    row["matched_keyword"] = keyword
    return row


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_scan_params(
    stars: object, limit: object, updated_since: object, updated_days: object
) -> tuple[int, int, str, str]:
    """夹取直调参数：stars / limit / updated_days 有上限，updated_since 必须可解析。"""
    stars_n = max(0, min(1_000_000, _as_int(stars, 50)))
    limit_n = max(1, min(10, _as_int(limit, 6)))
    days_n = max(1, min(3650, _as_int(updated_days, 365)))
    raw_since = str(updated_since or "").strip()
    note = ""
    if raw_since:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_since):
            note = f"updated_since={raw_since!r} 不是 YYYY-MM-DD，已回退到最近 {days_n} 天"
            raw_since = ""
        else:
            try:
                dt.date.fromisoformat(raw_since)
            except ValueError:
                note = f"updated_since={raw_since!r} 不是有效日期，已回退到最近 {days_n} 天"
                raw_since = ""
    return stars_n, limit_n, raw_since or default_since(days_n), note


def _scan_one(kw: str, gh_binary: str, stars: int, updated_since: str,
              limit: int, timeout: float) -> tuple[list[dict], str]:
    try:
        rows = gh_json(gh_binary, search_args(kw, stars, updated_since, limit), timeout)
    except (OSError, RuntimeError, ValueError) as exc:
        return [], str(exc)
    for row in rows:
        normalize_search_row(row, kw)
    return rows, ""


def search_candidates(
    keywords: list[str],
    stars: int = 50,
    limit: int = 6,
    updated_since: str = "",
    gh_binary: str = "gh",
    timeout: int = 30,
    updated_days: int = 365,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """检索 GitHub 候选仓库，返回结构化结果（不抛异常）。

    code=0 检索成功（rows 可能为空）· code=2 gh 不可用/参数非法
    code=3 全部关键词检索失败 · code=4 预算耗尽、检索未完成
    调用方必须把 code!=0 当成「没查成」，绝不等于「不存在」。

    `timeout` 是整次检索的总预算（登录检查 + 所有关键词），不是每个关键词各给一份。
    """
    clean, kw_error = normalize_keywords(keywords)
    result: dict[str, Any] = {
        "code": 0, "ok": False, "rows": [], "table": "", "keywords": clean,
        "failed": [], "skipped": [], "reason": "", "note": "", "hint": "",
    }
    if kw_error:
        result.update(code=2, reason=f"关键词非法：{kw_error}", hint=AUTH_HINT)
        return result
    if not clean:
        result.update(code=2, reason="没有可用的检索关键词（keywords 为空）。", hint=AUTH_HINT)
        return result

    exe = resolve_gh_path(gh_binary)
    if not exe:
        result.update(code=2,
                      reason="gh 不可用：只允许 PATH 里的 gh / gh.exe，且不接受当前目录里的同名文件。",
                      hint=AUTH_HINT)
        return result

    try:
        total_budget = max(5, min(300, int(timeout)))
    except (TypeError, ValueError):
        total_budget = 30
    now = clock or time.monotonic
    deadline = now() + total_budget
    # 登录检查最多拿 40% 预算（上限 10s），剩下的留给关键词；否则 auth 一慢，
    # 后面每个关键词都只剩 0.x 秒，会被误报成 code=3「gh 坏了」。
    auth_budget = max(2.0, min(10.0, total_budget * 0.4))
    ok, why = auth_ok(exe, auth_budget)
    if not ok:
        result.update(code=2, reason=f"gh 未就绪：{why}", hint=AUTH_HINT)
        return result
    if deadline - now() <= 0.2:
        result.update(
            code=2,
            reason=f"`gh auth status` 用完了总时限（{total_budget}s），还没开始检索关键词。",
            hint=AUTH_HINT,
        )
        return result

    stars_n, limit_n, since, note = normalize_scan_params(stars, limit, updated_since, updated_days)
    if note:
        result["note"] = note
    merged: dict[str, dict] = {}
    failed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    for kw in clean:
        remaining = deadline - now()
        if remaining <= 0.2:
            skipped.append(kw)
            errors.append(f"{kw}: 总时限 {total_budget}s 已用完，未检索（skipped）")
            continue
        rows, err = _scan_one(kw, exe, stars_n, since, limit_n, min(float(total_budget), remaining))
        if err:
            failed.append(kw)
            errors.append(f"{kw}: {err}")
        for row in rows:
            key = (row.get("url") or row.get("full_name") or "").rstrip("/").lower()
            if not key:
                continue  # 没有 URL 也没有 full_name 的行无法去重，也不能作为候选
            if key in merged:
                prev = merged[key].get("matched_keyword")
                if isinstance(prev, list):
                    if kw not in prev:
                        prev.append(kw)
                else:
                    merged[key]["matched_keyword"] = [prev, kw]
                continue
            row["matched_keyword"] = kw
            merged[key] = row

    rows = list(merged.values())
    for row in rows:
        row["_activity"] = activity_points(row.get("pushedAt", ""))
        row["_lic_score"], row["_lic_label"] = license_flags(row.get("license"))
        if not isinstance(row["matched_keyword"], list):
            row["matched_keyword"] = [row["matched_keyword"]]
    rows.sort(key=lambda r: (-r["_lic_score"], -r["_activity"], -(r.get("stargazersCount") or 0)))

    result["rows"] = rows
    result["table"] = render_table(rows) if rows else ""
    result["failed"] = failed
    result["skipped"] = skipped
    result["stars"], result["updated_since"], result["limit"] = stars_n, since, limit_n

    notes = [note] if note else []
    if skipped and not rows and len(failed) + len(skipped) == len(clean):
        result.update(
            code=4,
            reason="检索未完成（预算耗尽）：" + "; ".join(errors)[:600],
            hint=BUDGET_HINT,
        )
        return result
    if failed and len(failed) == len(clean):
        result.update(
            code=3,
            reason="全部关键词检索失败：" + "; ".join(errors)[:600],
            hint=FAIL_HINT,
        )
        return result
    if failed:
        notes.append(f"{len(failed)}/{len(clean)} 个关键词检索失败，结果可能偏少。")
    if skipped:
        notes.append(f"{len(skipped)}/{len(clean)} 个关键词因预算耗尽未检索（skipped），结果可能偏少。")
    if not rows and not failed and not skipped:
        notes.append(EMPTY_HINT)
    elif rows:
        notes.append(f"共 {len(rows)} 个去重候选（{len(clean)} 个关键词）。下一步：按 "
                     "references/repo-triage.md 读每个仓库的 CONTRIBUTING 与最近合并的 PR，做六维打分。")
    result["note"] = "；".join(notes)
    result["ok"] = result["code"] == 0
    return result


# ────────────────────────── 交付物机械检查 ──────────────────────────

SECTIONS = [
    ("found", ("What I found", "找到了什么")),
    ("why", ("Why this one", "为什么选它")),
    ("change", ("What I will add", "我要加什么")),
    ("risk", ("Alternatives", "备选与风险", "备选")),
]
VERDICTS = ("Join", "Extend", "Fork", "Build", "加入", "扩展", "分叉", "新建")
GATE_PREFIXES = ("Not reusable because:", "不可复用原因")
# 门禁只看「结论是哪一条」，不看全文有没有出现过 Build——备选段几乎总会提到它。
# ASCII 结论词必须整词匹配：rebuild / building / rebuild 不应命中 Build。
# 结论行允许 Markdown 装饰：`## 结论：Build`、`> **结论：Build**`、`结论：**Build**`。
CONCLUSION_RX = re.compile(
    r"^(?:最终|final\s+)?(?:结论|verdict|conclusion)\s*[:：]\s*[*_`> ]*"
    r"(Join|Extend|Fork|Build|加入|扩展|分叉|新建)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def _plain_line(line: str) -> str:
    """去掉行首的标题/引用/列表标记与成对的强调符号，得到可判定的一行。"""
    text = (line or "").strip()
    text = re.sub(r"^>\s*", "", text)
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = re.sub(r"^[-+*]\s+", "", text)
    text = text.strip()
    text = text.strip("*_` ")
    return text.strip()


def _heading_text(line: str) -> str:
    """标题行归一化：允许 `##`、`**`、`1.` 前缀与结尾冒号，但正文里的包含不算。"""
    text = _plain_line(line)
    text = re.sub(r"^\d+[.)、]\s*", "", text)
    return text.rstrip("：:").strip().casefold()


def _has_section(body: str, variants: tuple[str, ...]) -> bool:
    wanted = {v.casefold() for v in variants}
    return any(_heading_text(line) in wanted for line in body.splitlines())


def _conclusion_verdicts(text: str) -> list[str]:
    """按出现顺序返回所有显式结论行里的判决词。"""
    hits: list[str] = []
    for line in text.splitlines():
        match = CONCLUSION_RX.match(_plain_line(line))
        if match:
            hits.append(match.group(1).casefold())
    return hits


def _ascii_verdict_hits(text: str) -> list[str]:
    """没有结论行时的退化判定：只认 ASCII 整词，中文常见词不能全文扫。"""
    hits = []
    for verdict in VERDICTS:
        if verdict.isascii() and re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(verdict)}(?![A-Za-z0-9_])",
            text, re.IGNORECASE,
        ):
            hits.append(verdict.casefold())
    return hits


def check_deliverable(text: str) -> dict[str, Any]:
    """检查交付文本的结构完整性；不做语义判断，只查"该在的东西在不在"。"""
    missing: list[str] = []
    notes: list[str] = []
    body = text or ""

    for key, variants in SECTIONS:
        if not _has_section(body, variants):
            missing.append(f"section:{key}")

    explicit = _conclusion_verdicts(body)
    ascii_hits = _ascii_verdict_hits(body) if not explicit else []
    verdict_hit = list(dict.fromkeys(explicit + ascii_hits))
    if not verdict_hit:
        missing.append("verdict")
    else:
        notes.append("verdict: " + ", ".join(verdict_hit))

    # 多个结论行时取最后一条：备选段里的「结论：Build」不能盖掉最终结论。
    if explicit:
        verdict = explicit[-1]
    elif len(ascii_hits) == 1:
        verdict = ascii_hits[0]
    else:
        verdict = ""
        if ascii_hits:
            notes.append("结论不唯一：建议补一行「结论：Join/Extend/Fork/Build」；Build 门禁只按结论判定，不按备选段里的提及。")

    if verdict in ("build", "新建"):
        line_ok = any(
            _plain_line(line).lower().startswith(p.lower())
            for line in body.splitlines()
            for p in GATE_PREFIXES
        )
        if not line_ok:
            missing.append("gate_line")

    table_rows = sum(1 for line in body.splitlines() if line.strip().startswith("|"))
    if table_rows < 2:
        notes.append("no candidate table (| … | rows) — 有候选时应当给出表格")

    if re.search(r"\b\d{4}-\d{2}-\d{2}\b", body) is None:
        notes.append("no date seen — 引用仓库状态时建议带最近推送日期")

    return {"ok": not missing, "missing": missing, "notes": notes}


# ────────────────────────── 随包技能与校验器 ──────────────────────────

def skill_text(rel: str = "SKILL.md") -> str:
    path = os.path.join(SKILL_DIR, rel)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


_VERIFY_MODULE: Any = None


def _verify_module() -> Any:
    """按文件路径加载随包校验器，共用它的围栏/标记解析，避免两份实现漂移。"""
    global _VERIFY_MODULE
    if _VERIFY_MODULE is None:
        spec = importlib.util.spec_from_file_location("rbb_verify_localized", VERIFY_SCRIPT)
        if spec is None or spec.loader is None:
            raise RuntimeError("随包校验器不可用")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _VERIFY_MODULE = module
    return _VERIFY_MODULE


def core_rules_block() -> str:
    """取 SKILL.md 里 RBB:INVARIANTS 标记之间的正典规则段（整行锚定 + 跳过围栏示例）。"""
    text = skill_text("SKILL.md")
    return _verify_module().extract_block(
        text, "RBB:INVARIANTS:BEGIN", "RBB:INVARIANTS:END"
    ) or ""


def _looks_like_python(path: str) -> bool:
    """嵌入式宿主的 sys.executable 可能是宿主进程本身，不能再把它当解释器起一份。"""
    base = os.path.basename(path).lower()
    return bool(re.fullmatch(r"(?:python|pythonw)(?:\d+(?:\.\d+)*)?(?:\.exe)?", base)
                or base in {"py", "py.exe"})


def resolve_python(python_exe: str = "") -> str:
    """优先用真正的 Python 解释器；Windows 只有 py 启动器时也能找到。"""
    raw = str(python_exe or "").strip()
    if raw:
        return raw
    if sys.executable and os.path.isfile(sys.executable) and _looks_like_python(sys.executable):
        return sys.executable
    for name in ("python", "python3", "py"):
        found = which_path_only(name)
        if found:
            return found
    return "python"


def canonical_hash(python_exe: str = "", timeout: float = 30) -> tuple[str, str]:
    """跑随包校验器 --print-hash，返回 (hash, 错误信息)。"""
    py = resolve_python(python_exe)
    try:
        proc = subprocess.run(
            [py, "-X", "utf8", VERIFY_SCRIPT, "--print-hash", SKILL_MD],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=max(1, float(timeout)), shell=False,
        )
    except subprocess.TimeoutExpired:
        return "", f"规则哈希计算超时（>{timeout:g}s）"
    except OSError as exc:
        return "", f"规则哈希计算失败：{redact_text(exc)}"
    if proc.returncode != 0:
        return "", redact_text((proc.stderr or proc.stdout).strip())
    return redact_text(proc.stdout.strip(), 200), ""


def resolve_localized_path(target: str) -> tuple[str, str]:
    """把待校验路径限制在插件工作目录内，且必须是 .md。

    模型能传任意路径；不设防等于给它一个「读本机文件」的入口。相对路径按插件目录解析
    （与工具描述一致），realpath 会穿透 `..` 与符号链接，逃出工作目录的一律拒绝。
    """
    raw = str(target or "").strip()
    if not raw:
        return "", "缺少 path（待校验副本路径）。给插件目录内的 .md 路径再调一次。"
    candidate = raw if os.path.isabs(raw) else os.path.join(HERE, raw)
    real = os.path.realpath(os.path.expanduser(candidate))
    if not real.lower().endswith(".md"):
        return "", "只允许校验 .md 文件；其它扩展名一律拒绝。"
    root = os.path.realpath(HERE)
    try:
        inside = os.path.commonpath([real, root]) == root
    except ValueError:
        inside = False
    if not inside:
        return "", "目标不在插件工作目录内，已拒绝读取。把副本复制到插件目录内再校验。"
    if not os.path.isfile(real):
        return "", f"文件不存在：{os.path.basename(real)}（在插件工作目录内未找到）"
    return real, ""


def verify_localized(target: str, python_exe: str = "",
                     extra: list[str] | None = None, timeout: float = 30) -> dict[str, Any]:
    """校验一份本地化副本；rc=0 才算保住规则。路径、参数与输出都做收敛。

    `extra` 只允许 `--quiet`：`--canonical` 等能跳过声明检查的开关不能由模型侧注入。
    """
    real, path_err = resolve_localized_path(target)
    if path_err:
        return {"rc": 2, "out": "", "err": path_err[:400]}
    safe_extra = [arg for arg in (extra or []) if arg == "--quiet"]
    py = resolve_python(python_exe)
    args = [py, "-X", "utf8", VERIFY_SCRIPT, real, *safe_extra]
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=max(1, float(timeout)), shell=False,
        )
    except subprocess.TimeoutExpired:
        return {"rc": -1, "out": "", "err": f"校验超时（>{timeout:g}s）"}
    except OSError as exc:
        return {"rc": -1, "out": "", "err": f"找不到 Python 解释器或无法启动：{redact_text(exc, 200)}"}
    return {
        "rc": proc.returncode,
        "out": redact_text(proc.stdout.strip(), 800),
        "err": redact_text(proc.stderr.strip(), 400),
    }
