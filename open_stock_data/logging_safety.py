"""
日志安全工具。

统一对日志中的敏感信息做脱敏，避免 API key、token、Authorization 等内容泄露。
"""

from __future__ import annotations

import logging
import re
from typing import Any

_REDACTED = "<redacted>"
_SENSITIVE_KEYS = frozenset({
    "apikey",
    "api_key",
    "access_token",
    "refresh_token",
    "token",
    "authorization",
    "x-api-key",
    "secret",
    "signature",
    "sig",
    "password",
    "passwd",
})

_QUERY_PARAM_PATTERN = re.compile(
    r"(?i)([?&](?:apikey|api_key|access_token|refresh_token|token|x-api-key|signature|sig|secret|password|passwd)=)([^&#\s]+)"
)
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)(\b(?:apikey|api_key|access_token|refresh_token|token|x-api-key|secret|signature|sig|password|passwd)\b\s*[:=]\s*[\"']?)([^\"'\s,}\]]+)"
)
_AUTH_PATTERN = re.compile(
    r"(?i)(authorization\s*[:=]\s*[\"']?)(bearer\s+|basic\s+)?([^\"'\s,}]+)"
)

_INSTALLED = False


def redact_sensitive_text(value: str) -> str:
    """对字符串中的敏感字段进行脱敏。"""
    if not isinstance(value, str) or not value:
        return value

    value = _QUERY_PARAM_PATTERN.sub(r"\1" + _REDACTED, value)
    value = _KEY_VALUE_PATTERN.sub(r"\1" + _REDACTED, value)
    value = _AUTH_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2) or ''}{_REDACTED}",
        value,
    )
    return value


def sanitize_for_logging(value: Any) -> Any:
    """递归脱敏日志参数。"""
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_str = str(key).lower()
            if key_str in _SENSITIVE_KEYS:
                sanitized[key] = _REDACTED
            else:
                sanitized[key] = sanitize_for_logging(item)
        return sanitized

    if isinstance(value, tuple):
        return tuple(sanitize_for_logging(item) for item in value)

    if isinstance(value, list):
        return [sanitize_for_logging(item) for item in value]

    if isinstance(value, set):
        return {sanitize_for_logging(item) for item in value}

    if isinstance(value, str):
        return redact_sensitive_text(value)

    return value


def install_log_redaction() -> None:
    """安装全局日志记录脱敏器。"""
    global _INSTALLED
    if _INSTALLED:
        return

    previous_factory = logging.getLogRecordFactory()

    def redacting_factory(*args, **kwargs):
        args_list = list(args)

        if len(args_list) >= 6:
            args_list[4] = sanitize_for_logging(args_list[4])
            args_list[5] = sanitize_for_logging(args_list[5])

        if "msg" in kwargs:
            kwargs["msg"] = sanitize_for_logging(kwargs["msg"])
        if "args" in kwargs:
            kwargs["args"] = sanitize_for_logging(kwargs["args"])

        return previous_factory(*args_list, **kwargs)

    logging.setLogRecordFactory(redacting_factory)
    _INSTALLED = True
