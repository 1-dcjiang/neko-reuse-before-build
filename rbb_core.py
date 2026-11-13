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
import os
import re
import shutil
import subprocess
import tomllib
from typing import Any

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

# 兜底默认值与 plugin.toml / config.example.toml 保持一致；三处任一被改坏时仍能启动。
SETTINGS_DEFAULTS: dict[str, Any] = {
    "enable_github_scan": True,
    "gh_binary": "gh",
    "default_stars": 50,
    "default_limit": 6,
    "updated_days": 365,
    "scan_timeout": 30,
}


def manifest_settings(plugin_toml: str = "") -> dict[str, Any]:
    """读 plugin.toml 里的 [settings] 作为默认值；读不到就返回空表（不抛）。"""
    path = plugin_toml or PLUGIN_TOML
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except Exception:
        return {}
    section = data.get("settings")
    return section if isinstance(section, dict) else {}


def resolve_settings(section: Any = None, *, plugin_toml: str = "") -> dict[str, Any]:
    """把「代码兜底 → plugin.toml 默认 → 运行时配置段」三层合并成可用的六个值。

    宿主侧 `self.config.dump()` 不可用时（例如在没有真实 ctx 的测试环境），
    调用方传 None 即可，这里仍然返回一套可运行的值，不让插件因为读不到配置就启动失败。
    """
    merged: dict[str, Any] = dict(SETTINGS_DEFAULTS)
    merged.update({k: v for k, v in manifest_settings(plugin_toml).items() if k in SETTINGS_DEFAULTS})
    if isinstance(section, dict):
        merged.update({k: v for k, v in section.items() if k in SETTINGS_DEFAULTS})

    def _int(key: str, lo: int, hi: int) -> int:
        try:
            value = int(merged.get(key))
        except (TypeError, ValueError):
            value = int(SETTINGS_DEFAULTS[key])
        return max(lo, min(hi, value))

    gh_binary = str(merged.get("gh_binary") or "gh").strip() or "gh"
    enabled = merged.get("enable_github_scan")
    return {
        "enable_github_scan": enabled if isinstance(enabled, bool) else bool(enabled),
        "gh_binary": gh_binary,
        "default_stars": _int("default_stars", 0, 10_000_000),
        "default_limit": _int("default_limit", 1, 50),
        "updated_days": _int("updated_days", 1, 3650),
        "scan_timeout": _int("scan_timeout", 5, 300),
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
    except ValueError:
        return 0
    days = (dt.datetime.now(dt.timezone.utc) - pushed).days
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


def gh_json(gh_binary: str, args: list[str], timeout: int = 30) -> list[dict]:
    try:
        proc = _gh(gh_binary, args, timeout)
    except FileNotFoundError:
        raise RuntimeError("gh 可执行文件不在了（PATH 变了？）") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gh 超时（>{timeout}s）") from None
    if proc.returncode != 0:
        raise RuntimeError(f"gh 失败（exit {proc.returncode}）：{proc.stderr.strip()[:300]}")
    out = proc.stdout.strip()
    if not out:
        return []
    try:
        parsed = json.loads(out)
    except ValueError as exc:
        raise RuntimeError(f"gh 输出不是 JSON（{exc}）：{out[:200]}") from None
    return parsed if isinstance(parsed, list) else [parsed]


def auth_ok(gh_binary: str = "gh", timeout: int = 30) -> tuple[bool, str]:
    """只查检索要用的那一个 host：`gh auth status` 在任一已配置 host 掉线时会整体非零。"""
    host = os.environ.get("GH_HOST") or "github.com"
    try:
        proc = _gh(gh_binary, ["auth", "status", "-h", host], timeout)
    except FileNotFoundError:
        return False, "gh 可执行文件不在了（PATH 变了？）"
    except subprocess.TimeoutExpired:
        return False, f"`gh auth status` 超时（>{timeout}s）"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, f"{host}：{detail[0][:200] if detail else 'gh auth status 返回非零'}"
    return True, ""


def _scan_one(kw: str, gh_binary: str, stars: int, updated_since: str, limit: int, timeout: int) -> tuple[list[dict], str]:
    query = f"{kw} stars:>{stars} pushed:>{updated_since}"
    fields = "url,name,owner,stargazersCount,pushedAt,license,isArchived,language,openIssuesCount,description"
    try:
        rows = gh_json(
            gh_binary,
            ["search", "repos", query, "--archived=false", "--limit", str(limit), "--json", fields],
            timeout,
        )
    except RuntimeError as exc:
        return [], str(exc)
    for row in rows:
        owner = (row.get("owner") or {}).get("login", "")
        row["full_name"] = f"{owner}/{row.get('name', '')}" if owner else row.get("name", "")
        row["matched_keyword"] = kw
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
    """
    clean = [k.strip() for k in keywords if k and k.strip()]
    result: dict[str, Any] = {
        "code": 0, "ok": False, "rows": [], "table": "", "keywords": clean,
        "failed": [], "reason": "", "note": "", "hint": "",
    }
    if not clean:
        result.update(code=2, reason="没有可用的检索关键词（keywords 为空）。", hint=AUTH_HINT)
        return result

    exe = (gh_binary or "gh").strip() or "gh"
    if not (os.path.isabs(exe) and os.path.exists(exe)) and not shutil.which(exe):
        result.update(code=2,
                      reason=f"未找到 gh CLI（{exe}）。安装 GitHub CLI 并 `gh auth login` 后重试。",
                      hint=AUTH_HINT)
        return result

    ok, why = auth_ok(exe, timeout)
    if not ok:
        result.update(code=2, reason=f"gh 未就绪：{why}", hint=AUTH_HINT)
        return result

    since = (updated_since or "").strip() or default_since(updated_days)
    merged: dict[str, dict] = {}
    failed: list[str] = []
    errors: list[str] = []
    for kw in clean:
        rows, err = _scan_one(kw, exe, int(stars), since, int(limit), timeout)
        if err:
            failed.append(kw)
            errors.append(f"{kw}: {err}")
        for row in rows:
            key = (row.get("url") or row.get("full_name") or "").rstrip("/").lower()
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
CONCLUSION_RX = re.compile(
    r"(?:结论|verdict|conclusion)\s*[:：]\s*(Join|Extend|Fork|Build|加入|扩展|分叉|新建)",
    re.IGNORECASE,
)


def check_deliverable(text: str) -> dict[str, Any]:
    """检查交付文本的结构完整性；不做语义判断，只查"该在的东西在不在"。"""
    missing: list[str] = []
    notes: list[str] = []
    body = text or ""
    low = body.lower()

    for key, variants in SECTIONS:
        if not any(v.lower() in low for v in variants):
            missing.append(f"section:{key}")

    verdict_hit = [v for v in VERDICTS if v.lower() in low]
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


def canonical_hash(python_exe: str = "") -> tuple[str, str]:
    """跑随包校验器 --print-hash，返回 (hash, 错误信息)。"""
    py = python_exe or shutil.which("python") or "python"
    try:
        proc = subprocess.run(
            [py, "-X", "utf8", VERIFY_SCRIPT, "--print-hash", SKILL_MD],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return "", str(exc)
    if proc.returncode != 0:
        return "", (proc.stderr or proc.stdout).strip()[:300]
    return proc.stdout.strip(), ""


def verify_localized(target: str, python_exe: str = "", extra: list[str] | None = None) -> dict[str, Any]:
    """校验一份本地化副本；rc=0 才算保住规则。"""
    py = python_exe or shutil.which("python") or "python"
    args = [py, "-X", "utf8", VERIFY_SCRIPT, target, *(extra or [])]
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, shell=False,
        )
    except FileNotFoundError:
        return {"rc": -1, "out": "", "err": "找不到 Python 解释器"}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "out": "", "err": "校验超时（>60s）"}
    return {"rc": proc.returncode, "out": proc.stdout.strip(), "err": proc.stderr.strip()}
