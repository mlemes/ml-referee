"""Harness payload adapters: normalize hook events and format decisions."""

from __future__ import annotations

from collections.abc import Mapping

HARNESSES: tuple[str, ...] = ("claude", "codex", "devin", "agy", "opencode")


def detect_harness(raw: dict[str, object], environ: Mapping[str, str]) -> str:
    raise NotImplementedError


def normalize(harness: str, raw: dict[str, object]) -> list[dict[str, object]]:
    raise NotImplementedError


def format_decision(harness: str, allow: bool, reason: str) -> tuple[str, str, int]:
    raise NotImplementedError


def format_post_ack(harness: str) -> str:
    raise NotImplementedError
