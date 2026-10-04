"""Bounded parsing and physical file boundaries. Errors never carry input values."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import time
from pathlib import Path
from typing import Any


MAX_DOCUMENT_BYTES = 262_144
MAX_DEPTH = 12
MAX_NODES = 4096
MAX_RETENTION_SECONDS = 86_400
IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
RUN_ID = re.compile(r"[0-9a-f]{32}", re.ASCII)
DNS_NAME = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?",
    re.ASCII,
)


class Refusal(ValueError):
    def __init__(self, code: str):
        if not IDENTIFIER.fullmatch(code):
            raise ValueError("invalid_refusal_code")
        self.code = code
        super().__init__(code)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise Refusal(code)

def expected_run(value: Any) -> str:
    require(type(value) is str and RUN_ID.fullmatch(value) is not None, "expected_run_id_required")
    return value


def remaining_seconds(deadline: float) -> float:
    require(type(deadline) in (int, float) and math.isfinite(deadline), "invalid_capture_deadline")
    remaining = deadline - time.monotonic()
    require(remaining > 0, "capture_timeout")
    return remaining


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"),
        ).encode("ascii")
    except (ValueError, TypeError, RecursionError):
        raise Refusal("invalid_document") from None


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise Refusal("non_finite_json")


def document(raw: bytes, *, limit: int = MAX_DOCUMENT_BYTES) -> dict[str, Any]:
    require(type(raw) is bytes and 0 < len(raw) <= limit, "document_size_refused")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        if isinstance(error, Refusal):
            raise
        raise Refusal("malformed_document") from None
    require(type(result) is dict, "document_object_required")
    count = 0
    stack = [(result, 0)]
    while stack:
        value, depth = stack.pop()
        count += 1
        require(depth <= MAX_DEPTH and count <= MAX_NODES, "document_complexity_refused")
        if type(value) is dict:
            stack.extend((item, depth + 1) for item in value.values())
        elif type(value) is list:
            stack.extend((item, depth + 1) for item in value)
    return result


def exact_keys(value: Any, keys: set[str], code: str) -> None:
    require(type(value) is dict and set(value) == keys, code)


def physical(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    for ancestor in (*reversed(absolute.parents), absolute):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise Refusal("path_inspection_failed") from None
        require(
            not stat.S_ISLNK(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
            "path_alias_refused",
        )
        require(
            stat.S_ISDIR(info.st_mode) or ancestor == absolute and stat.S_ISREG(info.st_mode),
            "path_type_refused",
        )
    return absolute


def read_bytes(path: Path, *, limit: int = MAX_DOCUMENT_BYTES) -> bytes:
    target = physical(path)
    try:
        with target.open("rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "file_type_refused")
            require(0 < info.st_size <= limit, "document_size_refused")
            raw = stream.read(limit + 1)
            require(0 < len(raw) <= limit, "document_size_refused")
            return raw
    except OSError:
        raise Refusal("document_read_failed") from None


def read_document(path: Path, *, limit: int = MAX_DOCUMENT_BYTES) -> dict[str, Any]:
    return document(read_bytes(path, limit=limit), limit=limit)


def safe_host(value: str) -> str | None:
    """DNS grammar only; persisted host metadata must also be source-declared."""
    if type(value) is not str or not DNS_NAME.fullmatch(value):
        return None
    return value
