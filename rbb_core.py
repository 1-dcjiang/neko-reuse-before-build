"""reuse-before-build · 宿主无关的纯逻辑层（N.E.K.O 适配版）。

这一层不 import 任何 N.E.K.O SDK，只用标准库，因此可以在没有宿主的机器上直接跑测试。
它做四件事：
  1. search_candidates()  用 gh CLI 检索 GitHub 候选仓库，并把「检索失败」与「成功但 0 命中」分开；
  2. check_deliverable()  机械检查一段交付文本是否含四段格式、结论词与不可复用原因门禁行；
  3. verify_localized()   调用随包校验器检查本地化副本是否保住了不可变块；
  4. skill_text() / canonical_hash()  读取随包技能本体与正典哈希。

退出码语义沿用 CLI（scripts/repo_scan.py）：
  0 检索成功（可能 0 命中）· 2 gh 不可用（未安装/未登录/主机不通）· 3 gh 可用但全部关键词失败
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import shutil
import subprocess
import time
import tomllib
from typing import Any

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

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.join(HERE, "skills", "reuse-before-build")
VERIFY_SCRIPT = os.path.join(SKILL_DIR, "scripts", "verify_localized.py")
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")
PLUGIN_TOML = os.path.join(HERE, "plugin.toml")

# gh 只允许这些文件名：配置项 gh_binary 不能指向任意可执行文件。
GH_NAMES = {"gh", "gh.exe"}
GH_EXEC_NAMES = {"gh", "gh.exe", "gh.bat", "gh.cmd"}
_SECRET_RX = re.compile(
    r"(?i)(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"(?:token|password|secret)\s*[:=]\s*\S+)"
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
    """允许测试/库调用注入自定义安装路径，但文件名必须是 gh / gh.exe / gh.bat / gh.cmd。

    返回空串表示值非法，调用方把它当成「gh 不可用」，而不是悄悄换成另一个程序。
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    base = raw.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base in GH_EXEC_NAMES:
        return raw
    return ""

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


def activity_points(pushed_at: str) -> int:
    if not pushed_at:
        return 0
    try:
        pushed = dt.datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
        days = (dt.datetime.now(dt.timezone.utc) - pushed).days
    except (TypeError, ValueError, OverflowError):
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

def _gh(gh_binary: str, args: list[str], timeout: int) -> subprocess.CompletedProcess:
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
        raise RuntimeError(f"gh 输出不是 JSON（{exc}）：{text[:200]}") from None
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def gh_json(gh_binary: str, args: list[str], timeout: float = 30) -> list[dict]:
    exe = resolve_gh_executable(gh_binary)
    if not exe:
        raise RuntimeError("gh_binary 非法：只允许 gh / gh.exe / gh.bat / gh.cmd")
    try:
        proc = _gh(exe, args, timeout)
    except FileNotFoundError:
        raise RuntimeError("gh 可执行文件不在了（PATH 变了？）") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gh 超时（>{timeout:g}s）") from None
    except OSError as exc:
        raise RuntimeError(f"gh 启动失败：{redact_text(exc)}") from None
    if proc.returncode != 0:
        raise RuntimeError(f"gh 失败（exit {proc.returncode}）：{redact_text(proc.stderr.strip())}")
    return parse_search_output(proc.stdout)


def auth_ok(gh_binary: str = "gh", timeout: float = 30) -> tuple[bool, str]:
    """只查检索要用的那一个 host：`gh auth status` 在任一已配置 host 掉线时会整体非零。"""
    exe = resolve_gh_executable(gh_binary)
    if not exe:
        return False, "gh_binary 非法：只允许 gh / gh.exe / gh.bat / gh.cmd"
    host = os.environ.get("GH_HOST") or "github.com"
    try:
        proc = _gh(exe, ["auth", "status", "-h", host], timeout)
    except FileNotFoundError:
        return False, "gh 可执行文件不在了（PATH 变了？）"
    except subprocess.TimeoutExpired:
        return False, f"`gh auth status` 超时（>{timeout:g}s）"
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
    query = f"{keyword} stars:>{stars} pushed:>{updated_since}"
    fields = "url,name,owner,stargazersCount,pushedAt,license,isArchived,language,openIssuesCount,description"
    return ["search", "repos", "--archived=false", "--limit", str(limit), "--json", fields, "--", query]


def normalize_search_row(row: dict, keyword: str) -> dict:
    """补 full_name / matched_keyword，不改 gh 原字段。"""
    owner = (row.get("owner") or {}).get("login", "")
    row["full_name"] = f"{owner}/{row.get('name', '')}" if owner else row.get("name", "")
    row["matched_keyword"] = keyword
    return row


def _scan_one(kw: str, gh_binary: str, stars: int, updated_since: str,
              limit: int, timeout: float) -> tuple[list[dict], str]:
    try:
        rows = gh_json(gh_binary, search_args(kw, stars, updated_since, limit), timeout)
    except (OSError, RuntimeError) as exc:
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
) -> dict[str, Any]:
    """检索 GitHub 候选仓库，返回结构化结果（不抛异常）。

    code=0 检索成功（rows 可能为空）· code=2 gh 不可用 · code=3 全部关键词失败
    调用方必须把 code!=0 当成「没查成」，绝不等于「不存在」。

    `timeout` 是整次检索的总预算（登录检查 + 所有关键词），不是每个关键词各给一份。
    """
    clean = [k.strip() for k in keywords if k and k.strip()]
    result: dict[str, Any] = {
        "code": 0, "ok": False, "rows": [], "table": "", "keywords": clean,
        "failed": [], "reason": "", "note": "", "hint": "",
    }
    if not clean:
        result.update(code=2, reason="没有可用的检索关键词（keywords 为空）。", hint=AUTH_HINT)
        return result

    exe = resolve_gh_executable(gh_binary)
    if not exe:
        result.update(code=2,
                      reason="gh_binary 非法：只允许 gh / gh.exe / gh.bat / gh.cmd。",
                      hint=AUTH_HINT)
        return result
    if not (os.path.isabs(exe) and os.path.exists(exe)) and not shutil.which(exe):
        result.update(code=2,
                      reason=f"未找到 gh CLI（{exe}）。安装 GitHub CLI 并 `gh auth login` 后重试。",
                      hint=AUTH_HINT)
        return result

    try:
        total_budget = max(5, min(300, int(timeout)))
    except (TypeError, ValueError):
        total_budget = 30
    deadline = time.monotonic() + total_budget
    ok, why = auth_ok(exe, total_budget)
    if not ok:
        result.update(code=2, reason=f"gh 未就绪：{why}", hint=AUTH_HINT)
        return result

    since = (updated_since or "").strip() or default_since(updated_days)
    merged: dict[str, dict] = {}
    failed: list[str] = []
    errors: list[str] = []
    for kw in clean:
        remaining = deadline - time.monotonic()
        if remaining <= 0.2:
            failed.append(kw)
            errors.append(f"{kw}: 总时限 {total_budget}s 已用完")
            continue
        rows, err = _scan_one(kw, exe, int(stars), since, int(limit), min(float(total_budget), remaining))
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
    result["stars"], result["updated_since"], result["limit"] = int(stars), since, int(limit)

    if failed and len(failed) == len(clean):
        result.update(code=3,
                      reason="全部关键词检索失败：" + "; ".join(errors)[:600],
                      hint=FAIL_HINT)
        return result
    if failed:
        result["note"] = f"{len(failed)}/{len(clean)} 个关键词检索失败，结果可能偏少。"
    elif not rows:
        result["note"] = EMPTY_HINT
    else:
        result["note"] = (f"共 {len(rows)} 个去重候选（{len(clean)} 个关键词）。下一步：按 "
                          "references/repo-triage.md 读每个仓库的 CONTRIBUTING 与最近合并的 PR，做六维打分。")
    result["ok"] = True
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
CONCLUSION_RX = re.compile(
    r"(?:结论|verdict|conclusion)\s*[:：]\s*"
    r"(Join|Extend|Fork|Build|加入|扩展|分叉|新建)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def _verdict_present(text: str, verdict: str) -> bool:
    """ASCII 结论词按整词匹配，中文结论词按子串匹配。"""
    if verdict.isascii():
        return re.search(rf"(?<![A-Za-z0-9_]){re.escape(verdict)}(?![A-Za-z0-9_])",
                         text, re.IGNORECASE) is not None
    return verdict in text


def check_deliverable(text: str) -> dict[str, Any]:
    """检查交付文本的结构完整性；不做语义判断，只查"该在的东西在不在"。"""
    missing: list[str] = []
    notes: list[str] = []
    body = text or ""
    low = body.lower()

    for key, variants in SECTIONS:
        if not any(v.lower() in low for v in variants):
            missing.append(f"section:{key}")

    verdict_hit = [v for v in VERDICTS if _verdict_present(body, v)]
    if not verdict_hit:
        missing.append("verdict")
    else:
        notes.append("verdict: " + ", ".join(verdict_hit))

    # 判定结论：优先看「结论：X」这一行；没有结论行时，只有全文唯一结论词才算数。
    explicit = CONCLUSION_RX.search(body)
    if explicit:
        verdict = explicit.group(1).lower()
    elif len(verdict_hit) == 1:
        verdict = verdict_hit[0].lower()
    else:
        verdict = ""
        if verdict_hit:
            notes.append("结论不唯一：建议补一行「结论：Join/Extend/Fork/Build」；Build 门禁只按结论判定，不按备选段里的提及。")

    if verdict in ("build", "新建"):
        line_ok = any(
            any(line.strip().lower().startswith(p.lower()) for p in GATE_PREFIXES)
            for line in body.splitlines()
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


def core_rules_block() -> str:
    """取 SKILL.md 里 RBB:INVARIANTS 标记之间的正典规则段（整行锚定 + 跳过围栏示例）。"""
    text = skill_text("SKILL.md")
    lines = text.splitlines()
    fenced, in_fence, begins, ends = [], False, [], []
    for idx, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            fenced.append(True)
            continue
        fenced.append(in_fence)
        s = line.strip()
        if not in_fence and s == "<!-- RBB:INVARIANTS:BEGIN -->":
            begins.append(idx)
        if not in_fence and s == "<!-- RBB:INVARIANTS:END -->":
            ends.append(idx)
    candidates: list[str] = []
    for b in begins:
        e = next((x for x in ends if x > b), None)
        if e is not None:
            candidates.append("\n".join(lines[b + 1:e]).strip())
    if not candidates:
        return ""
    return max(candidates, key=lambda c: (len(re.findall(r"^(?:\d+)\.\s", c, re.M)), -candidates.index(c)))


def canonical_hash(python_exe: str = "", timeout: float = 60) -> tuple[str, str]:
    """跑随包校验器 --print-hash，返回 (hash, 错误信息)。"""
    py = python_exe or shutil.which("python") or "python"
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
    return proc.stdout.strip(), ""


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
                     extra: list[str] | None = None, timeout: float = 60) -> dict[str, Any]:
    """校验一份本地化副本；rc=0 才算保住规则。路径、参数与输出都做收敛。

    `extra` 只允许 `--quiet`：`--canonical` 等能跳过声明检查的开关不能由模型侧注入。
    """
    real, path_err = resolve_localized_path(target)
    if path_err:
        return {"rc": 2, "out": "", "err": path_err[:400]}
    safe_extra = [arg for arg in (extra or []) if arg == "--quiet"]
    py = python_exe or shutil.which("python") or "python"
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
    return {"rc": proc.returncode, "out": proc.stdout.strip()[:800], "err": redact_text(proc.stderr.strip(), 400)}
