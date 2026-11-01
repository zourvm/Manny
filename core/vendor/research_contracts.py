"""Public-data contracts shared by skill scripts.

The objects in this module are intentionally dependency-free. They make data
lineage explicit before heavier data adapters or quant engines are added.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any


class QualityFlag(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    FALLBACK = "fallback"
    STALE = "stale"
    CONFLICTING = "conflicting"
    UNVERIFIABLE = "unverifiable"
    ERROR = "error"


@dataclass(frozen=True)
class SourceRef:
    provider: str
    source_url: str | None = None
    source_path: str | None = None
    accessed_at: str = field(default_factory=lambda: date.today().isoformat())
    content_hash: str | None = None
    priority: str = "convenience"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "source_url": self.source_url,
            "source_path": self.source_path,
            "accessed_at": self.accessed_at,
            "content_hash": self.content_hash,
            "priority": self.priority,
        }


@dataclass(frozen=True)
class ProviderAttempt:
    provider: str
    status: str
    started_at: str
    ended_at: str | None = None
    rows: int | None = None
    message: str | None = None

    @classmethod
    def start(cls, provider: str) -> "ProviderAttempt":
        return cls(provider=provider, status="started", started_at=utc_now())

    def finish(self, status: str, rows: int | None = None, message: str | None = None) -> "ProviderAttempt":
        return ProviderAttempt(
            provider=self.provider,
            status=status,
            started_at=self.started_at,
            ended_at=utc_now(),
            rows=rows,
            message=message,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "rows": self.rows,
            "message": self.message,
        }


@dataclass
class DataEnvelope:
    request: dict[str, Any]
    rows: list[dict[str, Any]]
    provider_chain: list[ProviderAttempt]
    sources: list[SourceRef]
    data_cutoff: str | None = None
    quality_flags: list[QualityFlag] = field(default_factory=lambda: [QualityFlag.OK])
    warnings: list[str] = field(default_factory=list)
    schema_version: str = "2026-07-13.1"
    generated_at: str = field(default_factory=lambda: utc_now())

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "request": self.request,
            "data_cutoff": self.data_cutoff,
            "rows": self.rows,
            "quality_flags": [flag.value for flag in self.quality_flags],
            "warnings": self.warnings,
            "sources": [source.to_dict() for source in self.sources],
            "provider_chain": [attempt.to_dict() for attempt in self.provider_chain],
        }
        payload["raw_hash"] = stable_hash(self.rows)
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_cn_symbol(raw: str) -> str:
    text = raw.strip().upper()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) != 6:
        raise ValueError(f"Expected a six-digit China security/fund code, got: {raw}")
    if text.endswith(".SH") or text.startswith("SH") or digits.startswith(("5", "6", "9")):
        return f"{digits}.SH"
    if text.endswith(".SZ") or text.startswith("SZ") or digits.startswith(("0", "1", "2", "3")):
        return f"{digits}.SZ"
    if text.endswith(".BJ") or text.startswith("BJ") or digits.startswith(("4", "8")):
        return f"{digits}.BJ"
    return digits
