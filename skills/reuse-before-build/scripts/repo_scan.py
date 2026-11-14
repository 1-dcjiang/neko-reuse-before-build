#!/usr/bin/env python3
"""reuse-before-build · 候选仓库粗筛

用 gh CLI 对多个关键词跑 GitHub 仓库检索，去重后输出一张候选表，
供人工/模型进入六维评估（references/repo-triage.md）。

依赖：GitHub CLI（gh）已安装且 `gh auth status` 通过。不依赖任何第三方 Python 包。

退出码：
    0  检索成功（可能 0 命中——0 命中不等于"不存在"，见 stderr 提示）
    2  用法/参数错误（关键词非法、输出路径越界等）
    3  gh 可用但全部关键词检索失败（限流、参数、瞬时故障）
    4  gh 不可用：未安装、未登录/无权限，或连不上主机
    5  写输出文件失败
    6  只有部分关键词检索成功（结果可能偏少）

用法：
    python repo_scan.py "video highlight clip" "auto cut shorts" --stars 100
    python repo_scan.py "obsidian sync" --updated-since 2025-01-01 --limit 8 --md-out candidates.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time

try:  # Windows 控制台默认 cp936，中文会炸；能改就改
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


__version__ = "1.2.7"

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
_SECRET_RX = re.compile(
    r"(?i)(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"bearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"(?:access[_-]?token|api[_-]?key|token|password|secret)\s*[:=]\s*\S+)"
)
# URL userinfo（https://user:pass@host/...）和整行凭据头也要脱敏：
# 前者常见于把带凭据的仓库地址直接贴进日志，后者常见于 curl 复现命令。
_URL_CRED_RX = re.compile(r"(?i)(https?://)[^/\s:@]+(?::[^/\s@]*)?@")
_HEADER_RX = re.compile(r"(?i)((?:cookie|authorization)\s*[:=]\s*)[^\r\n]+")
# 关键词里出现检索限定符会覆盖脚本自己的 stars/pushed/archived 过滤条件。
_QUALIFIER_RX = re.compile(
    r"(?i)(?:^|\s)(?:stars|archived|pushed|created|updated|is|topic|language|license|"
    r"owner|repo|user|org|size|fork|mirror|template):"
)
_HOST_RX = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::\d{1,5})?$")


def which_path_only(name: str) -> str:
    """只从 PATH 解析可执行文件，绝不接受当前目录里的同名文件。

    `shutil.which()` 在 Windows 上会把当前目录补进搜索路径；在刚 clone 的第三方
    仓库里运行本脚本时，仓库自带的 `gh.exe`/`gh.bat` 就会被执行。这里显式过滤
    掉空条目与当前目录，并把过滤后的 PATH 传给 `shutil.which()`。
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


def validate_keyword(value: str) -> str:
    """关键词必须是纯文本：拒绝 NUL/控制字符、超长与能覆盖过滤条件的检索限定符。"""
    text = (value or "").strip()
    if not text:
        raise ValueError("关键词不能为空")
    if len(text) > 200:
        raise ValueError("单个关键词不能超过 200 字符")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError("关键词里不能有 NUL 或其它控制字符")
    if _QUALIFIER_RX.search(text):
        raise ValueError("关键词里不能带 stars:/pushed:/archived:/user:/org: 等检索限定符；"
                         "门槛请用 --stars / --updated-since")
    return text



def redact_text(value: object, limit: int = 300) -> str:
    """错误文本回给模型前脱敏：不把 token、口令和本机绝对路径原样吐出去。"""
    text = str(value or "")
    home = os.path.expanduser("~")
    if home:
        text = text.replace(home, "~")
    text = _URL_CRED_RX.sub(r"\1[redacted]@", text)
    text = _HEADER_RX.sub(r"\1[redacted]", text)
    text = _SECRET_RX.sub("[redacted]", text)
    return text[:limit]


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


def parse_search_output(out: str) -> list[dict]:
    """把 `gh search repos --json …` 的真实输出解析成行；供契约测试直接复用。

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


def gh_json(args: list[str], timeout: float = 60, gh_binary: str = "gh",
            host: str = "") -> list[dict]:
    env = None
    if host:
        env = dict(os.environ)
        env["GH_HOST"] = host
    try:
        proc = subprocess.run(
            [gh_binary, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            env=env,
        )
    except FileNotFoundError:
        raise RuntimeError("gh 可执行文件不在了（PATH 变了？）") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gh 超时（>{timeout:g}s）") from None
    except ValueError as exc:
        # subprocess 在参数含 NUL 时会抛 ValueError；必须在进入 Popen 前就变成可读错误。
        raise RuntimeError(f"gh 参数非法：{redact_text(exc)}") from None
    except OSError as exc:
        raise RuntimeError(f"gh 启动失败：{redact_text(exc)}") from None
    if proc.returncode != 0:
        raise RuntimeError(f"gh 失败（exit {proc.returncode}）：{redact_text(proc.stderr.strip())}")
    return parse_search_output(proc.stdout)


def search_args(keyword: str, stars: int, updated_since: str, limit: int) -> list[str]:
    """所有选项放 `--` 之前，检索串放 `--` 之后，防止 `-` 开头的关键词被当成 gh 选项。"""
    query = f"{keyword} stars:>={stars} pushed:>={updated_since}"
    fields = "url,name,owner,stargazersCount,pushedAt,license,isArchived,language,openIssuesCount,description"
    return ["search", "repos", "--archived=false", "--limit", str(limit), "--json", fields, "--", query]


def scan(keyword: str, stars: int, updated_since: str, limit: int,
         gh_binary: str = "gh", host: str = "", timeout: float = 60) -> tuple[list[dict], bool]:
    """返回 (rows, ok)。ok=False 表示这个关键词的检索本身失败，不是"没搜到"。

    限流/次限流只做一次 2 秒退避重试；预算不足时直接放弃，不把总时限吃光。
    """
    try:
        rows = gh_json(search_args(keyword, stars, updated_since, limit),
                       gh_binary=gh_binary, host=host, timeout=timeout)
    except RuntimeError as exc:
        text = str(exc).lower()
        if timeout > 4 and ("rate limit" in text or "429" in text or "secondary rate" in text):
            time.sleep(2)
            try:
                rows = gh_json(search_args(keyword, stars, updated_since, limit),
                               gh_binary=gh_binary, host=host, timeout=max(1.0, timeout - 2))
            except RuntimeError as retry_exc:
                print(f"[warn] 关键词 {keyword!r} 限流退避后仍失败：{retry_exc}", file=sys.stderr)
                return [], False
        else:
            print(f"[warn] 关键词 {keyword!r} 检索失败：{exc}", file=sys.stderr)
            return [], False
    for row in rows:
        owner = (row.get("owner") or {}).get("login", "")
        row["full_name"] = f"{owner}/{row.get('name', '')}" if owner else row.get("name", "")
        row["matched_keyword"] = keyword
    return rows, True


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


def auth_ok(host: str = "", gh_binary: str = "gh", timeout: float = 30) -> tuple[bool, str]:
    """先查登录态：未登录时搜索会失败，提前给出可执行的修复提示。

    默认只查检索要用的那一个 host（`--gh-host` > `GH_HOST` > github.com）：
    `gh auth status` 在任一已配置 host 失效时会整体返回非零，不限定 host 会把
    "另一个企业域名掉线"误报成本仓库不可用。要查企业 host 时用 `--gh-host` 或
    `GH_HOST` 显式指定；函数参数同优先级最高。`gh_binary` 由调用方传入，保证
    auth 检查与检索使用同一个可执行文件。
    """
    target = (host or os.environ.get("GH_HOST") or "github.com").strip()
    try:
        proc = subprocess.run(
            [gh_binary, "auth", "status", "-h", target],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(2.0, float(timeout)),
            shell=False,
        )
    except FileNotFoundError:
        return False, "gh 可执行文件不在了（PATH 变了？）"
    except subprocess.TimeoutExpired:
        return False, f"`gh auth status` 超时（>{timeout:g}s）"
    except OSError as exc:
        return False, f"gh 启动失败：{redact_text(exc, 200)}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, f"{target}：{redact_text(detail[0], 200) if detail else 'gh auth status 返回非零'}"
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


def _stars_arg(value: str) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--stars 必须是整数") from exc
    if not 0 <= n <= 1_000_000:
        raise argparse.ArgumentTypeError("--stars 必须在 0..1000000")
    return n


def _limit_arg(value: str) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--limit 必须是整数") from exc
    if not 1 <= n <= 10:
        raise argparse.ArgumentTypeError("--limit 必须在 1..10")
    return n


def _since_arg(value: str) -> str:
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--updated-since 必须是 YYYY-MM-DD") from exc
    return value


def _host_arg(value: str) -> str:
    host = (value or "").strip()
    if not host:
        return ""
    if not _HOST_RX.match(host) or ".." in host or host.startswith(".") or host.endswith("."):
        raise argparse.ArgumentTypeError(
            "--gh-host 只接受 hostname 或 hostname:port，不能带协议、路径或账号密码"
        )
    return host


def _keyword_arg(value: str) -> str:
    try:
        return validate_keyword(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _out_root(value: str) -> str:
    root = os.path.abspath(value or ".")
    if not os.path.isdir(root):
        raise ValueError(f"--out-dir 不是已存在的目录：{root}")
    return os.path.realpath(root)


def _safe_out_path(value: str, root: str, flag: str) -> str:
    """把 --md-out / --json-out 限定在输出根目录内，并拒绝覆盖已有文件。

    绝对路径也必须在 root 内；`..` 与符号链接逃逸用 realpath + commonpath 判定。
    返回绝对路径；空值返回空串。
    """
    if not value:
        return ""
    candidate = os.path.abspath(value if os.path.isabs(value) else os.path.join(root, value))
    real_root = os.path.realpath(root)
    real_candidate = os.path.realpath(candidate)
    try:
        inside = os.path.commonpath([real_root, real_candidate]) == real_root
    except ValueError:  # Windows 跨盘符
        inside = False
    if not inside:
        raise ValueError(f"{flag} 必须写在 --out-dir（{root}）内，拒绝写到：{candidate}")
    parts = [p.lower() for p in os.path.normcase(real_candidate).replace("\\", "/").split("/")]
    if ".git" in parts:
        raise ValueError(f"{flag} 不能写进 .git 目录（钩子/配置目录）：{candidate}")
    if os.path.lexists(candidate):
        raise ValueError(f"{flag} 指向已存在的文件，拒绝覆盖：{candidate}")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description="GitHub 候选仓库粗筛（reuse-before-build）")
    parser.add_argument("keywords", nargs="+", type=_keyword_arg,
                        help="一个或多个检索串（英文效果最好；不能带检索限定符）")
    parser.add_argument("--stars", type=_stars_arg, default=100, help="星数下限（含边界），默认 100")
    parser.add_argument(
        "--updated-since",
        type=_since_arg,
        default=default_since(),
        help="最近推送日期下限，默认一年前（YYYY-MM-DD）",
    )
    parser.add_argument("--limit", type=_limit_arg, default=8, help="每个关键词返回条数，默认 8")
    parser.add_argument("--out-dir", default=".", help="输出根目录，默认当前目录；--md-out/--json-out 必须位于其下")
    parser.add_argument("--gh-host", type=_host_arg, default="", help="GitHub host，默认 GH_HOST 或 github.com")
    parser.add_argument("--timeout", type=int, default=120,
                        help="整次检索的总预算秒数（默认 120，范围 10–900）")
    parser.add_argument("--max-keywords", type=int, default=5, help="关键词数量上限（默认 5，最大 10）")
    parser.add_argument("--md-out", default="", help="把 Markdown 表格写入该文件（相对 --out-dir）")
    parser.add_argument("--json-out", default="", help="把原始结果写入该 JSON 文件（相对 --out-dir）")
    args = parser.parse_args()

    if len(args.keywords) > max(1, min(10, args.max_keywords)):
        print(f"[error] 关键词给了 {len(args.keywords)} 个，上限 {min(10, args.max_keywords)} 个。"
              "合并同义词、拆成多轮再跑。", file=sys.stderr)
        return 2
    if not 10 <= args.timeout <= 900:
        print("[error] --timeout 必须在 10..900 秒", file=sys.stderr)
        return 2

    try:
        out_root = _out_root(args.out_dir)
        md_path = _safe_out_path(args.md_out, out_root, "--md-out")
        json_path = _safe_out_path(args.json_out, out_root, "--json-out")
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    if md_path and json_path and os.path.normcase(md_path) == os.path.normcase(json_path):
        print("[error] --md-out 与 --json-out 不能指向同一个文件", file=sys.stderr)
        return 2

    deadline = time.monotonic() + args.timeout
    gh_host = args.gh_host.strip()
    gh_binary = which_path_only("gh")
    if not gh_binary:
        print(
            "未找到 gh CLI（只查 PATH，不接受当前目录里的 gh.exe/gh.bat）。"
            "安装 GitHub CLI 并 `gh auth login` 后重试；"
            "没有 gh 时改用 references/discovery-playbook.md 的网页检索串。",
            file=sys.stderr,
        )
        return 4

    auth_budget = max(2.0, min(10.0, args.timeout * 0.4))
    ok, why = auth_ok(gh_host, gh_binary=gh_binary, timeout=auth_budget)
    if not ok:
        print(f"{AUTH_HINT}\n详情：{why}", file=sys.stderr)
        return 4
    if deadline - time.monotonic() <= 0.2:
        print(f"[error] `gh auth status` 用完了总时限（{args.timeout}s），还没开始检索。", file=sys.stderr)
        return 4

    merged: dict[str, dict] = {}
    failed = []
    for kw in args.keywords:
        remaining = deadline - time.monotonic()
        if remaining <= 0.2:
            print(f"[warn] 总时限 {args.timeout}s 已用完，关键词 {kw!r} 未检索。", file=sys.stderr)
            failed.append(kw)
            continue
        rows, hit = scan(kw, args.stars, args.updated_since, args.limit,
                         gh_binary=gh_binary, host=gh_host,
                         timeout=min(60.0, remaining))
        if not hit:
            failed.append(kw)
        for row in rows:
            key = (row.get("url") or row.get("full_name") or "").rstrip("/").lower()
            if not key:
                continue
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

    if failed and len(failed) == len(args.keywords):
        print(f"AUTH/网络问题：{len(failed)} 个关键词全部检索失败。{FAIL_HINT}", file=sys.stderr)
        if md_path or json_path:
            print("检索失败，未写出 --md-out / --json-out（没有可用结果，避免空的候选表被当成'查过了'）。",
                  file=sys.stderr)
        return 3

    print(table)

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

    try:
        if md_path:
            with open(md_path, "x", encoding="utf-8", newline="\n") as fh:
                fh.write(table + "\n")
        if json_path:
            clean_rows = [{k: v for k, v in row.items() if k != "description"} for row in rows]
            with open(json_path, "x", encoding="utf-8", newline="\n") as fh:
                json.dump(clean_rows, fh, ensure_ascii=False, indent=2)
    except FileExistsError:
        print("[error] 输出文件在写入前被别人创建，拒绝覆盖；请换一个文件名。", file=sys.stderr)
        return 5
    except OSError as exc:
        print(f"[error] 写输出文件失败：{redact_text(exc, 200)}", file=sys.stderr)
        return 5
    return 6 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
