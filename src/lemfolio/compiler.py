"""Isolated TeX builds against immutable project exports, with durable receipts."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid

from .store import MAX_FILE, MAX_FILES, MAX_TOTAL, StoreError, validate_path


def _synctex_boxes(output):
    """Parse `synctex view` records; a record is a page plus h/v/W/H in PDF points."""
    boxes, current = [], {}
    for line in output.splitlines():
        key, separator, value = line.partition(":")
        if not separator or key not in {"Page", "h", "v", "W", "H"}:
            continue
        if key == "Page":
            current = {}
        try:
            current[key] = int(value) if key == "Page" else float(value)
        except ValueError:
            current = {}
            continue
        if len(current) == 5:
            boxes.append({"page": current["Page"], "x": current["h"], "y": current["v"] - current["H"],
                          "width": current["W"], "height": current["H"]})
            current = {}
    return boxes[:64]


def _synctex_source(output, source_hashes):
    """Parse `synctex edit`, accepting only files that belong to this build's snapshot."""
    fields = {}
    for line in output.splitlines():
        key, separator, value = line.partition(":")
        if separator and key in {"Input", "Line", "Column"} and key not in fields:
            fields[key] = value.strip()
    try:
        path = posixpath.normpath(fields["Input"])
        line, column = int(fields["Line"]), int(fields.get("Column", "-1"))
    except (KeyError, ValueError):
        return None
    path = path[len("/src/"):] if path.startswith("/src/") else path
    return (path, line, max(column, 0)) if path in source_hashes and line >= 1 else None


class CompilerError(StoreError):
    """Admission failures are HTTP-compatible without depending on the API."""


OUTPUT_LIMIT = 256 * 1024 * 1024
QUERY_OUTPUT_LIMIT = 2 * 1024 * 1024


class Compiler:
    def __init__(self, root: Path, store, image: str | None = None, timeout: int = 120,
                 *, max_workers: int = 2, max_builds: int = 200,
                 max_storage_bytes: int = 4 * 1024 * 1024 * 1024):
        self.root = Path(root) / "builds"
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.image = image or os.environ.get("LEMFOLIO_TEX_IMAGE", "lemfolio/tex:0.1")
        self.timeout = timeout
        self.lock = threading.RLock()
        self.cancelled: set[str] = set()
        self.workers: dict[str, threading.Thread] = {}
        self.queries: set[str] = set()
        self.query_events: dict[str, threading.Event] = {}
        self.reservations: dict[str, int] = {}
        self.pending_cleanup: set[str] = set()
        self.max_workers = max(1, int(max_workers))
        self.max_builds = max(1, int(max_builds))
        self.max_storage_bytes = max(1, int(max_storage_bytes))
        self.closed = False
        for receipt in self.root.glob("*/receipt.json"):
            record = json.loads(receipt.read_text(encoding="utf-8"))
            active = record["status"] in {"queued", "running"}
            if active or (record.get("cleanup") or {}).get("status") == "failed":
                cleanup = self._remove_container(record["id"])
                if active:
                    record.update(status="cancelled" if record.get("cancellation_requested") else "interrupted",
                                  error="Application restarted during compilation.", completed_at=time.time())
                record["cleanup"] = cleanup
                if cleanup["status"] == "failed":
                    self.pending_cleanup.add("lemfolio-" + record["id"])
                self._save(record)
        for marker in self.root.glob("*/synctex-*.json"):
            query = json.loads(marker.read_text(encoding="utf-8"))
            if re.fullmatch(r"lemfolio-synctex-[a-f0-9]{32}", query.get("container_name", "")):
                cleanup = self._remove_named_container(query["container_name"])
                if cleanup["status"] in {"absent", "removed"}:
                    marker.unlink()
                else:
                    self.pending_cleanup.add(query["container_name"])
                    query.update(status="cleanup_failed", cleanup=cleanup)
                    self._save_query(marker, query)

    def _path(self, build_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", build_id):
            raise ValueError("Invalid build ID")
        path = self.root / build_id
        if not path.is_dir():
            raise FileNotFoundError("Build not found")
        return path

    def _save(self, record):
        with self.lock:
            path = self.root / record["id"] / "receipt.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)

    @staticmethod
    def _save_query(path, record):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        os.replace(temporary, path)

    def status(self):
        # an installed copy has no scripts/ and builds the image from its Start menu entry
        build = "Build TeX image from the Lemfolio Start menu entry" if (Path(__file__).resolve().parents[2] / "build-tex-image.cmd").is_file() else "run scripts/setup.ps1 -BuildTex"
        try:
            result = subprocess.run(["docker", "image", "inspect", self.image, "--format", "{{.Id}}"], capture_output=True, timeout=10, text=True)
            return {"available": result.returncode == 0, "image": self.image, "image_id": result.stdout.strip() if result.returncode == 0 else None, "isolation": "Docker, network=none, read-only root, no shell escape", "setup": f"{build[0].upper()}{build[1:]} if the image is missing."}
        except (OSError, subprocess.TimeoutExpired):
            return {"available": False, "image": self.image, "setup": f"Start Docker Desktop, then {build[0].lower()}{build[1:]}."}

    @staticmethod
    def _identity(record):
        fields = ("project_id", "snapshot_id", "state_revision", "base_revision",
                  "source_hashes", "main_file", "engine", "image_id")
        return hashlib.sha256(json.dumps({key: record.get(key) for key in fields},
                              ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _disk_size(directory):
        total = 0
        for path in directory.rglob("*"):
            try:
                info = path.lstat()
            except FileNotFoundError:
                # latexmk can remove intermediate files during the walk. A
                # vanished file consumes no ongoing output quota.
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
        return total

    def _admit(self, input_size=0, *, query=False):
        if self.closed:
            raise CompilerError("Compiler is shutting down.", 503)
        if self.pending_cleanup:
            raise CompilerError("A previous container could not be confirmed stopped; restart after Docker is available to retry cleanup.", 503)
        if len(self.workers) + len(self.queries) >= self.max_workers:
            raise CompilerError("Compilation capacity is busy; retry after an active job finishes.", 429)
        if not query and len(list(self.root.glob("*/receipt.json"))) >= self.max_builds:
            raise CompilerError("Build retention limit reached; archive build storage before retrying.", 507)
        reserve = QUERY_OUTPUT_LIMIT if query else input_size + OUTPUT_LIMIT + 3 * 64 * 1024 * 1024
        # Existing bytes plus all admitted future output remain inside a finite
        # storage budget. This is conservative while another job is producing.
        reserved = sum(self.reservations.values()) + reserve
        if (self._disk_size(self.root) + reserved > self.max_storage_bytes
                or reserved > shutil.disk_usage(self.root).free):
            raise CompilerError("Build storage budget is exhausted.", 507)
        return reserve

    def submit(self, project_id: str, main_file=None, engine="pdflatex", snapshot=None):
        if engine not in {"pdflatex", "xelatex", "lualatex"}:
            raise ValueError("Unsupported TeX engine")
        snapshot = self.store.capture_project(project_id) if snapshot is None else snapshot
        files = dict(snapshot["files"])
        project = snapshot["project"]
        if project["id"] != project_id or snapshot["main_file"] != project["main_file"]:
            raise ValueError("Build snapshot project metadata does not match")
        if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_TOTAL:
            raise ValueError("Build snapshot exceeds source limits")
        for name, data in files.items():
            validate_path(name)
            if not isinstance(data, bytes) or len(data) > MAX_FILE:
                raise ValueError("Invalid build source bytes")
        if len({name.casefold() for name in files}) != len(files) or any(
                str(parent) in files for name in files for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise ValueError("Conflicting build source paths")
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        revision = hashlib.sha256(json.dumps(hashes, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        state = self.store._state_revision(revision, project["main_file"], project["generation"])
        if (revision != snapshot["revision"] or hashes != snapshot["source_hashes"]
                or state != snapshot["state_revision"] or state != project["state_revision"]
                or not re.fullmatch(r"[a-f0-9]{32}", snapshot["snapshot_id"])):
            raise ValueError("Build snapshot identity does not match its source bytes")
        main_file = project["main_file"] if main_file is None else main_file
        part = PurePosixPath(main_file)
        if part.is_absolute() or ".." in part.parts or not re.fullmatch(r"[\w /().-]+\.tex", main_file) or "\\" in main_file:
            raise ValueError("Invalid main TeX path")
        if main_file not in hashes:
            raise ValueError("Main TeX file does not exist")
        build_id = uuid.uuid4().hex
        job = self.root / build_id
        work = job / "work"
        with self.lock:
            reserve = self._admit(sum(map(len, files.values())))
            try:
                work.mkdir(parents=True)
                for name, data in files.items():
                    dest = work.joinpath(*PurePosixPath(name).parts)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                (job / "out").mkdir()
                record = {"id": build_id, "project_id": project_id, "base_revision": revision,
                          "snapshot_id": snapshot["snapshot_id"], "state_revision": state,
                          "source_hashes": hashes, "main_file": main_file, "engine": engine,
                          "status": "queued", "created_at": time.time(), "image": self.image,
                          "image_id": None, "artifacts": [], "error": None}
                record["identity_digest"] = self._identity(record)
                self._save(record)
                worker = threading.Thread(target=self._build, args=(record,), daemon=True)
                self.workers[build_id] = worker
                self.reservations[build_id] = reserve
                worker.start()
                return dict(record)
            except BaseException:
                self.workers.pop(build_id, None)
                self.reservations.pop(build_id, None)
                if job.exists():
                    shutil.rmtree(job)
                raise

    def _remove_container(self, build_id):
        return self._remove_named_container(f"lemfolio-{build_id}")

    def _remove_named_container(self, name):
        try:
            result = subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
            if result.returncode == 0:
                return {"status": "removed"}
            error = result.stderr.decode("utf-8", "replace") if isinstance(result.stderr, bytes) else str(result.stderr)
            return {"status": "absent" if "no such container" in error.lower() else "failed"}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"status": "failed", "error": type(exc).__name__}

    @staticmethod
    def _stop_client(process):
        try:
            if process is None or process.poll() is not None:
                return {"status": "exited"}
            process.kill()
            process.wait(timeout=10)
            return {"status": "stopped"}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"status": "failed", "error": type(exc).__name__}

    def _build(self, record):
        build_id = record["id"]
        job = self._path(build_id)
        process = None
        try:
            image = self.status()
            if not image["available"]:
                record.update(status="unavailable", error=image["setup"])
                return
            with self.lock:
                if build_id in self.cancelled:
                    record.update(status="cancelled", cancellation_requested=True)
                    return
                record.update(status="running", image_id=image["image_id"])
                record["identity_digest"] = self._identity(record)
                self._save(record)
            engine = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}[record["engine"]]
            command = ["docker", "run", "--name", f"lemfolio-{build_id}", "--rm", "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=96", "--memory=768m", "--cpus=2", "--ulimit", "fsize=67108864:67108864", "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m", "--mount", f"type=bind,source={job / 'work'},target=/src,readonly", "--mount", f"type=bind,source={job / 'out'},target=/out", "--workdir", "/src", "--env", "TEXMFOUTPUT=/out", "--user", "65534:65534", image["image_id"], "latexmk", engine, "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", "-synctex=1", "-no-shell-escape", "-norc", "-outdir=/out", "./" + record["main_file"]]
            # Stream to disk to avoid unbounded subprocess output in application memory.
            with (job / "console.log").open("wb") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                try:
                    deadline = time.monotonic() + self.timeout
                    while process.poll() is None:
                        if time.monotonic() > deadline:
                            raise subprocess.TimeoutExpired(command, self.timeout)
                        size = self._disk_size(job / 'out') + (job / 'console.log').stat().st_size
                        if size > OUTPUT_LIMIT or build_id in self.cancelled:
                            self._remove_container(build_id)
                            record["client_cleanup"] = self._stop_client(process)
                            record.update(status="cancelled" if build_id in self.cancelled else "output_limit", error="Compilation cancelled" if build_id in self.cancelled else "Compilation output exceeded 256 MiB")
                            break
                        time.sleep(.1)
                    else:
                        code = process.returncode
                        record.update(status="success" if code == 0 else "failed", exit_code=code)
                        if self._disk_size(job / 'out') + (job / 'console.log').stat().st_size > OUTPUT_LIMIT:
                            record.update(status="output_limit", error="Compilation output exceeded 256 MiB")
                except subprocess.TimeoutExpired:
                    self._remove_container(build_id)
                    record["client_cleanup"] = self._stop_client(process)
                    record.update(status="timeout", error="Compilation exceeded time limit")
            stem = Path(record["main_file"]).stem
            out = job / "out"
            artifacts = []
            for suffix in (".pdf", ".log", ".synctex.gz"):
                path = out / (stem + suffix)
                if path.is_file() and not path.is_symlink() and path.stat().st_size <= 64 * 1024 * 1024:
                    target = job / (stem + suffix)
                    shutil.copyfile(path, target)
                    artifacts.append({"name": target.name, "size": target.stat().st_size, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
            record["artifacts"] = artifacts
            if record["status"] == "success" and not any(x["name"].endswith(".pdf") for x in artifacts):
                record.update(status="failed", error="Compiler returned success without a PDF")
            with (job / "console.log").open("rb") as tail:
                tail.seek(max(0, tail.seek(0, 2) - 100000))
                record["log"] = tail.read(100000).decode("utf-8", errors="replace")
        except Exception as exc:
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            cleanup = self._remove_container(build_id)
            client_cleanup = self._stop_client(process)
            with self.lock:
                if build_id in self.cancelled:
                    record.update(status="cancelled", cancellation_requested=True)
                record["completed_at"] = time.time()
                record["cleanup"] = cleanup
                record["client_cleanup"] = client_cleanup
                if cleanup["status"] == "failed" or client_cleanup["status"] == "failed":
                    self.pending_cleanup.add("lemfolio-" + build_id)
                try:
                    self._save(record)
                finally:
                    self.workers.pop(build_id, None)
                    self.reservations.pop(build_id, None)
                    self.cancelled.discard(build_id)

    def inspect(self, build_id):
        with self.lock:
            return self._receipt_identity(json.loads((self._path(build_id) / "receipt.json").read_text(encoding="utf-8")))

    @staticmethod
    def _receipt_identity(record):
        legacy = any(not record.get(key) for key in ("snapshot_id", "state_revision", "identity_digest"))
        for key in ("snapshot_id", "state_revision", "identity_digest"):
            record.setdefault(key, None)
        record["legacy_identity"] = legacy
        return record

    def list_builds(self, project_id):
        with self.lock:
            records = [self._receipt_identity(json.loads(p.read_text(encoding="utf-8"))) for p in self.root.glob("*/receipt.json")]
        return sorted([r for r in records if r["project_id"] == project_id], key=lambda r: r["created_at"], reverse=True)

    def interrupt(self, build_id):
        with self.lock:
            record = self.inspect(build_id)
            if record["status"] not in {"queued", "running"}:
                return record
            self.cancelled.add(build_id)
            record["cancellation_requested"] = True
            self._save(record)
        self._remove_container(build_id)
        return record

    def artifact(self, build_id, name):
        record = self.inspect(build_id)
        item = next((x for x in record["artifacts"] if x["name"] == name), None)
        if not item:
            raise FileNotFoundError("Build artifact not found")
        path = self._path(build_id) / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Build artifact integrity check failed")
        return path

    def synctex(self, build_id, source, line=1, column=1):
        """Forward search: where a saved source line appears in this build's PDF."""
        record = self.inspect(build_id)
        if source not in record["source_hashes"] or line < 1 or column < 1:
            raise ValueError("Invalid source position")
        pdf = Path(record["main_file"]).stem + ".pdf"
        output, code = self._synctex_query(record, ["view", "-i", f"{line}:{column}:/src/{source}", "-o", f"/out/{pdf}"])
        return {**self._synctex_identity(record), "status": "matched" if code == 0 and "Page:" in output else "no_match",
                "boxes": _synctex_boxes(output) if code == 0 else [], "file": source, "line": line, "column": column}

    def synctex_inverse(self, build_id, page, x, y):
        """Inverse search: which source line produced a point on this build's PDF."""
        record = self.inspect(build_id)
        if page < 1 or not (math.isfinite(x) and math.isfinite(y)) or x < 0 or y < 0:
            raise ValueError("Invalid PDF position")
        pdf = Path(record["main_file"]).stem + ".pdf"
        output, code = self._synctex_query(record, ["edit", "-o", f"{page}:{x:.3f}:{y:.3f}:/out/{pdf}"])
        target = _synctex_source(output, record["source_hashes"]) if code == 0 else None
        return {**self._synctex_identity(record), "status": "matched" if target else "no_match", "boxes": [],
                "file": target[0] if target else None, "line": target[1] if target else None,
                "column": target[2] if target else None}

    def _synctex_identity(self, record):
        return {"build_id": record["id"], "base_revision": record["base_revision"],
                "state_revision": record.get("state_revision"), "snapshot_id": record.get("snapshot_id"),
                "identity_digest": record.get("identity_digest")}

    def _synctex_query(self, record, arguments):
        build_id = record["id"]
        if record["status"] != "success":
            raise ValueError("SyncTeX requires a successful build")
        job = self._path(build_id)
        query_id = uuid.uuid4().hex
        name = "lemfolio-synctex-" + query_id
        output_path = job / ("synctex-" + query_id + ".log")
        marker = job / ("synctex-" + query_id + ".json")
        with self.lock:
            self.reservations[query_id] = self._admit(query=True)
            self.queries.add(name)
            self.query_events[name] = threading.Event()
        command = ["docker", "run", "--name", name, "--rm", "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=128m", "--pids-limit=32", "--cpus=1", "--ulimit", "fsize=2097152:2097152", "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m", "--user", "65534:65534", "--mount", f"type=bind,source={job / 'work'},target=/src,readonly", "--mount", f"type=bind,source={job / 'out'},target=/out,readonly", record["image_id"], "synctex", *arguments]
        process = None
        try:
            self._save_query(marker, {"container_name": name, "status": "running"})
            with output_path.open("wb") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 15
                while process.poll() is None:
                    if self.closed or time.monotonic() > deadline or output_path.stat().st_size > QUERY_OUTPUT_LIMIT:
                        raise CompilerError("SyncTeX cancelled, timed out or exceeded its output limit.", 503)
                    time.sleep(.05)
            if output_path.stat().st_size > QUERY_OUTPUT_LIMIT:
                raise CompilerError("SyncTeX exceeded its output limit.", 413)
            with output_path.open("rb") as log:
                output = log.read(QUERY_OUTPUT_LIMIT).decode("utf-8", "replace")
            return output, process.returncode
        finally:
            try:
                cleanup = self._remove_named_container(name)
                client_cleanup = self._stop_client(process)
                output_path.unlink(missing_ok=True)
                if cleanup["status"] in {"removed", "absent"} and client_cleanup["status"] != "failed":
                    marker.unlink(missing_ok=True)
                else:
                    self._save_query(marker, {"container_name": name, "status": "cleanup_failed", "cleanup": cleanup,
                                              "client_cleanup": client_cleanup})
                    with self.lock:
                        self.pending_cleanup.add(name)
            finally:
                with self.lock:
                    self.queries.discard(name)
                    self.reservations.pop(query_id, None)
                    self.query_events.pop(name).set()

    def close(self):
        deadline = time.monotonic() + 40
        with self.lock:
            self.closed = True
            workers = list(self.workers.items())
            queries = list(self.query_events.items())
        for name, _ in queries:
            self._remove_named_container(name)
        for build_id, _ in workers:
            self.interrupt(build_id)
        for _, worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        for _, completed in queries:
            completed.wait(timeout=max(0, deadline - time.monotonic()))
