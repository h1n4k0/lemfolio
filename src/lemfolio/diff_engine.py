"""Bounded JSON subprocess bridge to the actual vendored Keel Rust diff model."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


def binary_path() -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    default = Path(__file__).resolve().parents[2] / "target" / "diff" / "release" / ("lemfolio-diff" + suffix)
    return Path(os.environ.get("LEMFOLIO_DIFF_BINARY", str(default)))


def describe_diff(unified: str, context: int = 3) -> dict:
    if not isinstance(unified, str) or len(unified.encode("utf-8")) > 4 * 1024 * 1024:
        return {"status": "invalid", "engine": "keel-rust", "error": "Diff exceeds 4 MiB or is not text"}
    executable = binary_path()
    if not executable.is_file():
        return {"status": "unavailable", "engine": "keel-rust", "reason": "Build the lemfolio-diff native binary before requesting rich diffs."}
    request = json.dumps({"unified": unified, "context": min(100, max(0, int(context)))}, ensure_ascii=False).encode("utf-8")
    try:
        result = subprocess.run([str(executable)], input=request, capture_output=True, timeout=10, check=False)
        if result.returncode or len(result.stdout) > 32 * 1024 * 1024:
            return {"status": "unavailable", "engine": "keel-rust", "reason": "Native diff process failed or exceeded output limits."}
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or payload.get("status") not in {"available", "invalid"}:
            raise ValueError("Invalid native diff response")
        return payload
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"status": "unavailable", "engine": "keel-rust", "reason": "Native diff process could not return a valid result."}
