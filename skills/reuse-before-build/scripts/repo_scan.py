#!/usr/bin/env python3
"""reuse-before-build · 候选仓库粗筛

用 gh CLI 对多个关键词跑 GitHub 仓库检索，去重后输出一张候选表，
供人工/模型进入六维评估（references/repo-triage.md）。

依赖：GitHub CLI（gh）已安装且 `gh auth status` 通过。不依赖任何第三方 Python 包。

退出码：
    0  检索成功（可能 0 命中——0 命中不等于"不存在"，见 stderr 提示）
    2  gh 不可用：未安装、未登录/无权限，或连不上主机
    3  gh 可用但全部关键词检索失败（限流、参数、瞬时故障）

用法：
    python repo_scan.py "video highlight clip" "auto cut shorts" --stars 100
    python repo_scan.py "obsidian sync" --updated-since 2025-01-01 --limit 8 --md-out candidates.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys

try:  # Windows 控制台默认 cp936，中文会炸；能改就改
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


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


def default_since(today: dt.date | None = None) -> str:
    """默认"最近一年"用 365 天回退，避免闰日 2/29 的 replace(year-1) 崩掉。"""
    today = today or dt.date.today()
    return (today - dt.timedelta(days=365)).isoformat()


def md_cell(value: object) -> str:
    """表格单元格转义：竖线会切断 Markdown 表格，换行会切断行。"""
    if value is None:
        return "-"
    text = str(value)
    if text == "":
        return "-"
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def gh_json(args: list[str], timeout: int = 60) -> list[dict]:
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
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
        return json.loads(out)
    except ValueError as exc:
        raise RuntimeError(f"gh 输出不是 JSON（{exc}）：{out[:200]}") from None


def scan(keyword: str, stars: int, updated_since: str, limit: int) -> tuple[list[dict], bool]:
    """返回 (rows, ok)。ok=False 表示这个关键词的检索本身失败，不是"没搜到"。"""
    query = f"{keyword} stars:>{stars} pushed:>{updated_since}"
    fields = "url,name,owner,stargazersCount,pushedAt,license,isArchived,language,openIssuesCount,description"
    try:
        rows = gh_json(
            ["search", "repos", query, "--archived=false", "--limit", str(limit), "--json", fields]
        )
    except RuntimeError as exc:
        print(f"[warn] 关键词 {keyword!r} 检索失败：{exc}", file=sys.stderr)
        return [], False
    for row in rows:
        owner = (row.get("owner") or {}).get("login", "")
        row["full_name"] = f"{owner}/{row.get('name', '')}" if owner else row.get("name", "")
        row["matched_keyword"] = keyword
    return rows, True


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


def auth_ok() -> tuple[bool, str]:
    """先查登录态：未登录时搜索会失败，提前给出可执行的修复提示。

    只查检索要用的那一个 host（GH_HOST 优先，否则 github.com）：`gh auth status`
    在任一已配置 host 失效时会整体返回非零，不限定 host 会把"另一个企业域名掉线"
    误报成本仓库不可用。
    """
    host = os.environ.get("GH_HOST") or "github.com"
    try:
        proc = subprocess.run(
            ["gh", "auth", "status", "-h", host],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
        )
    except FileNotFoundError:
        return False, "gh 可执行文件不在了（PATH 变了？）"
    except subprocess.TimeoutExpired:
        return False, "`gh auth status` 超时（>30s）"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, f"{host}：{detail[0][:200] if detail else 'gh auth status 返回非零'}"
    return True, ""


def render_table(rows: list[dict]) -> str:
    header = "| 仓库 | ★ | 最近推送 | 语言 | 许可 | open issues | 命中关键词 | URL |"
    sep = "|---|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for row in rows:
        pushed = (row.get("pushedAt") or "")[:10] or "-"
        if row["_activity"] == 0:
            pushed = f"{pushed} ⚠停滞"
        lines.append(
            "| {name} | {stars} | {pushed} | {lang} | {lic} | {issues} | {kw} | {url} |".format(
                name=md_cell(row.get("full_name")),
                stars=md_cell(row.get("stargazersCount") or 0),
                pushed=md_cell(pushed),
                lang=md_cell(row.get("language")),
                lic=md_cell(row["_lic_label"]),
                issues=md_cell(row.get("openIssuesCount")),
                kw=md_cell(", ".join(row.get("matched_keyword") or [])),
                url=md_cell(row.get("url")),
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="GitHub 候选仓库粗筛（reuse-before-build）")
    parser.add_argument("keywords", nargs="+", help="一个或多个检索串（英文效果最好）")
    parser.add_argument("--stars", type=int, default=100, help="星数下限，默认 100")
    parser.add_argument(
        "--updated-since",
        default=default_since(),
        help="最近推送日期下限，默认一年前（YYYY-MM-DD）",
    )
    parser.add_argument("--limit", type=int, default=8, help="每个关键词返回条数，默认 8")
    parser.add_argument("--md-out", default="", help="把 Markdown 表格写入该文件")
    parser.add_argument("--json-out", default="", help="把原始结果写入该 JSON 文件")
    args = parser.parse_args()

    if not shutil.which("gh"):
        print(
            "未找到 gh CLI。安装 GitHub CLI 并 `gh auth login` 后重试；"
            "没有 gh 时改用 references/discovery-playbook.md 的网页检索串。",
            file=sys.stderr,
        )
        return 2

    ok, why = auth_ok()
    if not ok:
        print(f"{AUTH_HINT}\n详情：{why}", file=sys.stderr)
        return 2

    merged: dict[str, dict] = {}
    failed = []
    for kw in args.keywords:
        rows, hit = scan(kw, args.stars, args.updated_since, args.limit)
        if not hit:
            failed.append(kw)
        for row in rows:
            key = (row.get("url") or row.get("full_name") or "").rstrip("/").lower()
            if key in merged:
                if isinstance(merged[key]["matched_keyword"], list):
                    merged[key]["matched_keyword"].append(kw)
                else:
                    merged[key]["matched_keyword"] = [merged[key]["matched_keyword"], kw]
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

    table = render_table(rows)
    print(table)

    if failed and len(failed) == len(args.keywords):
        print(f"AUTH/网络问题：{len(failed)} 个关键词全部检索失败。{FAIL_HINT}", file=sys.stderr)
        if args.md_out or args.json_out:
            print("检索失败，未写出 --md-out / --json-out（没有可用结果，避免空的候选表被当成'查过了'）。",
                  file=sys.stderr)
        return 3

    if failed:
        print(f"[warn] {len(failed)}/{len(args.keywords)} 个关键词检索失败，结果可能偏少。{FAIL_HINT}",
              file=sys.stderr)

    if not rows:
        print(
            f"检索成功，但 {args.stars}★ / {args.updated_since} 之后这组关键词 0 命中。"
            "0 命中不等于不存在：换英文同义词、降 --stars、放宽 --updated-since 再跑一轮，"
            "或用 references/discovery-playbook.md 的生态检索补查。",
            file=sys.stderr,
        )
    else:
        print(
            f"\n共 {len(rows)} 个去重候选（{len(args.keywords)} 个关键词）。"
            "下一步：按 references/repo-triage.md 打开每个仓库读 CONTRIBUTING 与最近合并的 PR，做六维打分。",
            file=sys.stderr,
        )

    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(table + "\n")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
