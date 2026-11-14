#!/usr/bin/env python3
"""插件内核与随包 CLI 共用的错误文本脱敏规则。

只处理要回传给模型或终端的错误文本，不用于改写仓库内容。新增规则必须在这里
统一维护，并由两个调用方的契约测试覆盖，避免 N.E.K.O 适配层再次漏项。
"""

from __future__ import annotations

import os
import re

_SECRET_RX = re.compile(
    r"(?i)(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"bearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"(?:access[_-]?token|api[_-]?key|authorization|token|password|secret)\s*[:=]\s*\S+)"
)
_URL_CRED_RX = re.compile(r"(?i)(https?://)[^/\s:@]+(?::[^/\s@]*)?@")
_HEADER_RX = re.compile(r"(?i)((?:cookie|authorization)\s*[:=]\s*)[^\r\n]+")


def redact_text(value: object, limit: int = 300) -> str:
    """隐藏常见凭据与 home 路径，再按字符数截断。

    URL userinfo 与 Cookie / Authorization 头必须先于通用 token 规则处理；
    后者保留 ``https://`` 前缀和字段名，方便定位错误类型。
    """
    text = str(value or "")
    home = os.path.expanduser("~")
    if home:
        text = text.replace(home, "~")
    text = _URL_CRED_RX.sub(r"\1[redacted]@", text)
    text = _HEADER_RX.sub(r"\1[redacted]", text)
    text = _SECRET_RX.sub("[redacted]", text)
    return text[:limit]
