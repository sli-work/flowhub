"""Shared UTC time helpers independent of a runtime implementation."""
from datetime import UTC, datetime


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
