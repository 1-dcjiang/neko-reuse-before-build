#!/usr/bin/env python3
"""插件内核与随包 CLI 共用的 GitHub 检索辅助逻辑。"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil


def _load_redaction():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "redaction.py")
    spec = importlib.util.spec_from_file_location("rbb_redaction_for_gh_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("随包脱敏模块不可用")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


redact_text = _load_redaction().redact_text

# 关键词里的检索限定符会覆盖调用方自己的 stars/pushed/archived 过滤条件。
_QUALIFIER_RX = re.compile(
    r"(?i)(?:^|\s)(?:stars|archived|pushed|created|updated|is|topic|language|license|"
    r"owner|repo|user|org|size|fork|mirror|template):"
)
KEYWORD_MAX = 200


def which_path_only(name: str) -> str:
    """只从 PATH 解析可执行文件，并对 Windows 的隐式当前目录结果再做硬校验。"""
    raw = os.environ.get("PATH", "")
    cwd = os.path.normcase(os.path.realpath(os.curdir))
    entries = [
        part for part in raw.split(os.pathsep)
        if part and os.path.normcase(os.path.realpath(os.path.abspath(part))) != cwd
    ]
    if not entries:
        return ""
    path = os.pathsep.join(entries)
    # Windows 优先真正的 gh.exe，不让 gh.cmd/gh.bat 抢先进入 cmd 参数解释层。
    names = ("gh.exe", name) if os.name == "nt" and name.lower() == "gh" else (name,)
    for candidate in names:
        found = shutil.which(candidate, path=path) or ""
        if not found or not os.path.isabs(found):
            continue
        real = os.path.normcase(os.path.realpath(found))
        if os.path.dirname(real) == cwd:
            continue
        return found
    return ""


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
        raise ValueError("关键词里不能带 stars:/pushed:/archived:/user:/org: 等检索限定符；"
                         "门槛请用 --stars / --updated-since")
    return text


def parse_search_output(out: str) -> list[dict]:
    """解析 gh 的 JSON 输出，只保留 dict 行；错误片段先脱敏再返回。"""
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


def search_args(keyword: str, stars: int, updated_since: str, limit: int) -> list[str]:
    """所有选项放 `--` 之前，检索串放 `--` 之后，避免关键词被当成 gh 选项。"""
    query = f"{keyword} stars:>={stars} pushed:>={updated_since}"
    fields = "url,name,owner,stargazersCount,pushedAt,license,isArchived,language,openIssuesCount,description"
    return ["search", "repos", "--archived=false", "--limit", str(limit), "--json", fields, "--", query]


def normalize_search_row(row: dict, keyword: str) -> dict:
    """补 full_name / matched_keyword，不改 gh 原字段。"""
    owner = (row.get("owner") or {}).get("login", "")
    row["full_name"] = f"{owner}/{row.get('name', '')}" if owner else row.get("name", "")
    row["matched_keyword"] = keyword
    return row
