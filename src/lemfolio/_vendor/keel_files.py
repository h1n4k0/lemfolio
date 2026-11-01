"""Selected file primitives copied from h1n4k0/keel.

Source: src/keel/files.py, commit ee316400899344c9c98e93e81ff7a2c63b2ff20f
Git blob: b0d773d3639141fad05020a8d6b144bbcb67e584
Repository owner explicitly authorized reuse in the Lemfolio development chat.
The selected function/class bodies are unchanged. Imports and constants only are
reduced to remove the Keel Core dependency. See docs/keel-reuse.md.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass

REPARSE_POINT = 0x400


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT)


@dataclass(frozen=True)
class Read:
    """One actual read of one path. `outcome`: present · absent · inaccessible · read_failed · unstable."""

    outcome: str
    operation: str
    digest: str | None = None
    size: int | None = None
    error: str | None = None
    content: bytes | None = None


def read_stable(path: str, *, cap: int, keep: bool, retries: int = 2) -> Read:
    """Read the file and say what was found — distinguishing a file that is not there from one that could not be
    read. The size and the modification time are taken before and after; content that changed under the read is
    read again, and reported as `unstable` if it will not hold still."""
    for _ in range(retries + 1):
        try:
            before = os.lstat(path)
        except FileNotFoundError:
            return Read("absent", "stat")
        except NotADirectoryError:
            return Read("absent", "stat")
        except PermissionError as exc:
            return Read("inaccessible", "stat", error=f"permission_denied: {_oserror(exc)}")
        except OSError as exc:
            return Read("read_failed", "stat", error=_oserror(exc))
        if _is_link(before):
            return Read("inaccessible", "stat", error="link_not_followed")
        if not stat.S_ISREG(before.st_mode):
            return Read("inaccessible", "stat", error="not_a_regular_file")
        if before.st_size > cap:
            return Read("read_failed", "stat", error=f"too_large: {before.st_size} bytes, limit {cap}")
        try:
            with open(path, "rb") as fh:
                content = fh.read(cap + 1)
            after = os.lstat(path)
        except FileNotFoundError:
            continue                                # it went away while being read: look again
        except PermissionError as exc:              # no permission, or another process holds it exclusively
            return Read("inaccessible", "stat+read+sha256", error=f"permission_denied_or_in_use: {_oserror(exc)}")
        except OSError as exc:
            return Read("read_failed", "stat+read+sha256", error=_oserror(exc))
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns) == (len(content), before.st_mtime_ns):
            return Read("present", "stat+read+sha256", sha256(content), len(content), None, content if keep else None)
    return Read("unstable", "stat+read+sha256", error="the content changed while it was read")


def _oserror(exc: OSError) -> str:
    return f"{type(exc).__name__}[{getattr(exc, 'winerror', None) or exc.errno}]"
