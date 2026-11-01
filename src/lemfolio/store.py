"""Local paper storage with content-bound edits and recoverable file transactions.

The application owns this directory. A hostile process running as the same OS user
can race path checks; this is not an OS security boundary. TeX execution belongs
in the separate compiler sandbox, never in this module.
"""
from __future__ import annotations

import contextlib
import difflib
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from ._vendor.keel_files import _is_link, read_stable, sha256

TEXT_EXTENSIONS = {".tex", ".bib", ".sty", ".cls", ".bst", ".txt", ".md", ".csv", ".tsv", ".dat", ".tikz",
                   ".bbl", ".clo", ".def", ".cfg", ".fd", ".bbx", ".cbx", ".lbx", ".ldf", ".ltx", ".pgf", ".ist"}
ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".eps", ".pdf", ".otf", ".ttf"}
MAX_FILE = 20 * 1024 * 1024
MAX_TEXT = 2 * 1024 * 1024
MAX_TOTAL = 200 * 1024 * 1024
MAX_FILES = 2000
_ID = re.compile(r"^[0-9a-f]{32}$")
_LOCKS: dict[str, threading.RLock] = {}
_LOCK_GUARD = threading.Lock()



_DEFAULT_IMPORT = "Imported paper"
_ARCHIVE_NOISE = {"__macosx", ".git", ".ds_store", "thumbs.db", "desktop.ini", "export-errors.json"}
# A Prism all-project export can carry chat archives; they are records, not paper sources.
_ARCHIVE_RECORDS = {"conversations"}


def _archive_name(path: str) -> str | None:
    """The project path for a ZIP entry, or None when it is not part of a paper.

    Exports carry files a paper project cannot hold, such as hidden files and build scripts.
    They are left out and reported, so one of them cannot sink the import. A path that
    tries to leave the project still fails validation in the caller."""
    parts = path.replace("\\", "/").split("/")
    if any(part.casefold() in _ARCHIVE_NOISE or part.casefold() in _ARCHIVE_RECORDS for part in parts):
        return None
    if any(part.startswith(".") and part not in {".", ".."} for part in parts):
        return None
    if ".." not in parts and PurePosixPath(parts[-1]).suffix.lower() not in TEXT_EXTENSIONS | ASSET_EXTENSIONS:
        return None
    return path


def _strip_common_root(files: dict[str, bytes]) -> tuple[dict[str, bytes], str]:
    """An export wraps everything in one folder; the paper's own paths start below it."""
    tops = {path.split("/", 1)[0] for path in files}
    if len(tops) != 1 or any("/" not in path for path in files):
        return files, ""
    top = tops.pop()
    return {path.split("/", 1)[1]: content for path, content in files.items()}, top


def _declares_document(content: bytes) -> bool:
    text = content.decode("utf-8", "ignore")
    uncommented = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("%"))
    return "\\documentclass" in uncommented or "\\documentstyle" in uncommented


def _root_document(files: dict[str, bytes], rc: list[str] = ()) -> str:
    """The file to compile: what latexmk is told to build, else main.tex, else the shallowest full document."""
    for text in rc:
        found = re.search(r"""@default_files\s*=\s*\(\s*['"]([^'"]+)['"]""", text)
        if found:
            named = found[1] if found[1].lower().endswith(".tex") else found[1] + ".tex"
            if named in files:
                return named
    if "main.tex" in files:
        return "main.tex"
    documents = [path for path, content in files.items() if path.lower().endswith(".tex") and _declares_document(content)]
    if documents:
        return min(documents, key=lambda path: (path.count("/"), path))
    return next(path for path in files if path.lower().endswith(".tex"))


def _project_groups(files: dict[str, bytes]) -> dict[str, dict[str, bytes]]:
    """Several top-level folders that each hold a full document are several projects. A paper
    with a root document and chapter folders stays one project."""
    if any("/" not in path and path.lower().endswith(".tex") and _declares_document(content) for path, content in files.items()):
        return {}
    groups: dict[str, dict[str, bytes]] = {}
    for path, content in files.items():
        if "/" in path:
            top, rest = path.split("/", 1)
            groups.setdefault(top, {})[rest] = content
    papers = {top: group for top, group in groups.items()
              if any(path.lower().endswith(".tex") and _declares_document(content) for path, content in group.items())}
    return papers if len(papers) >= 2 else {}


class StoreError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: bytes) -> str:
    return sha256(value)


def _link(info: os.stat_result) -> bool:
    return _is_link(info)


def validate_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 240 or "\\" in value:
        raise StoreError("Use a nonempty relative path with forward slashes.")
    parts = value.split("/")
    if any(not p or p in {".", ".."} or p.startswith(".") or p[-1:] in {" ", "."}
           or any(ord(c) < 32 or c in '<>:"|?*' for c in p)
           or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p)
           for p in parts):
        raise StoreError("Unsafe or reserved project path.")
    path = PurePosixPath(value)
    if path.is_absolute() or path.suffix.lower() not in TEXT_EXTENSIONS | ASSET_EXTENSIONS:
        raise StoreError("Only paper source, bibliography, data, font and image files are supported.")
    return value


class Store:
    def __init__(self, root: Path | str):
        self.root = Path(root).absolute()
        for ancestor in [*reversed(self.root.parents), self.root]:
            if ancestor.exists() and _link(ancestor.lstat()):
                raise StoreError("The data directory must not traverse symlinks or reparse points.")
        self.root.mkdir(parents=True, exist_ok=True)
        self.projects_root = self.root / "projects"
        self.journal_root = self.root / "transactions"
        for directory in (self.projects_root, self.journal_root):
            if directory.exists() and _link(directory.lstat()):
                raise StoreError("Managed data directories must not be links.")
            directory.mkdir(exist_ok=True)
        self.database = self.root / "store.sqlite3"
        if self.database.exists() and (_link(self.database.lstat()) or self.database.stat().st_nlink != 1):
            raise StoreError("The database must be a regular private file.")
        with _LOCK_GUARD:
            self._lock = _LOCKS.setdefault(str(self.root).casefold(), threading.RLock())
        self._pending_journals: dict[sqlite3.Connection, list[Path]] = {}
        self._pending_projects: dict[sqlite3.Connection, list[Path]] = {}
        self._git_journals: dict[sqlite3.Connection, dict[str, Path]] = {}
        with contextlib.closing(self._connect()) as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, main_file TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, revision TEXT NOT NULL,
                    commit_hash TEXT NOT NULL, message TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS snapshot_projects ON snapshots(project_id,created_at);
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, payload TEXT NOT NULL,
                    digest TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,
                    decided_at REAL, result_revision TEXT);
                CREATE TABLE IF NOT EXISTS transactions (id TEXT PRIMARY KEY);
            """)
            # Keep content revisions compatible with existing exports. Managed
            # state has a separate generation so an A -> B -> A edit is visible.
            for table, name, declaration in (
                ("projects", "generation", "INTEGER NOT NULL DEFAULT 0"),
                ("snapshots", "main_file", "TEXT"),
                ("snapshots", "generation", "INTEGER"),
                ("snapshots", "state_revision", "TEXT"),
                ("proposals", "result_state_revision", "TEXT"),
            ):
                if name not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        with self._transaction() as db:
            self._recover(db)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @contextlib.contextmanager
    def _transaction(self):
        with self._lock:
            db = self._connect()
            journals: list[Path] = []
            projects: list[Path] = []
            self._pending_journals[db] = journals
            self._pending_projects[db] = projects
            self._git_journals[db] = {}
            try:
                db.execute("BEGIN IMMEDIATE")
                self._recover(db)
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                # A caller can fail after _apply has returned (for example while
                # recording an approval), and commit itself can fail. The outer
                # transaction owns restoration, not just _apply's stack frame.
                for directory in reversed(journals):
                    if directory.exists() and not db.execute(
                            "SELECT 1 FROM transactions WHERE id=?", (directory.name,)).fetchone():
                        self._rollback(directory)
                        self._remove_tree(directory)
                for directory in reversed(projects):
                    if directory.exists() and not db.execute(
                            "SELECT 1 FROM projects WHERE id=?", (directory.name,)).fetchone():
                        self._remove_tree(directory)
                raise
            finally:
                self._pending_journals.pop(db, None)
                self._pending_projects.pop(db, None)
                self._git_journals.pop(db, None)
                db.close()

    def _project(self, db, project_id: str) -> sqlite3.Row:
        if not isinstance(project_id, str) or not _ID.fullmatch(project_id):
            raise StoreError("Project not found.", 404)
        row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise StoreError("Project not found.", 404)
        directory = self.projects_root / project_id
        if not directory.is_dir() or _link(directory.lstat()):
            raise StoreError("Project directory is missing or unsafe.", 409)
        return row

    def project_dir(self, project_id: str) -> Path:
        with contextlib.closing(self._connect()) as db:
            self._project(db, project_id)
        return self.projects_root / project_id

    def _path(self, project_id: str, name: str) -> Path:
        validate_path(name)
        root = self.projects_root / project_id
        target = root.joinpath(*name.split("/"))
        if not root.is_dir() or _link(root.lstat()):
            raise StoreError("Project root is unsafe.", 409)
        current = root
        for index, part in enumerate(name.split("/")):
            if current.exists():
                matches = [p.name for p in current.iterdir() if p.name.casefold() == part.casefold()]
                if matches and matches != [part]:
                    raise StoreError("Path differs from an existing file only by case.", 409)
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            if _link(info) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                raise StoreError("Links and reparse points are not permitted in projects.", 409)
            if index < len(name.split("/")) - 1 and not stat.S_ISDIR(info.st_mode):
                raise StoreError("A parent component is not a directory.", 409)
            if index == len(name.split("/")) - 1 and not stat.S_ISREG(info.st_mode):
                raise StoreError("Project files must be regular files.", 409)
        return target

    def _read(self, path: Path) -> bytes:
        try:
            before = path.lstat()
            if _link(before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise StoreError("Unsafe file.", 409)
            # Keel's stable read is reused unchanged. Lemfolio adds inode and
            # hard-link checks around it for its managed mutable paper tree.
            observed = read_stable(str(path), cap=MAX_FILE, keep=True)
            if observed.outcome == "absent":
                raise StoreError("File not found.", 404)
            if observed.outcome != "present" or observed.content is None:
                status = 413 if (observed.error or "").startswith("too_large") else 409
                raise StoreError("File could not be read stably: " + (observed.error or observed.outcome), status)
            data = observed.content
            after = path.lstat()
            keys = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            if keys(before) != keys(after) or _link(after) or after.st_nlink != 1 or len(data) != after.st_size:
                raise StoreError("File changed while reading; retry.", 409)
            return data
        except FileNotFoundError as exc:
            raise StoreError("File not found.", 404) from exc

    def _scan(self, project_id: str) -> dict[str, bytes]:
        root = self.projects_root / project_id
        if not root.is_dir() or _link(root.lstat()):
            raise StoreError("Project root is missing or unsafe.", 409)
        result: dict[str, bytes] = {}
        total = 0
        seen: set[str] = set()
        def scan_error(error):
            raise StoreError("Project tree cannot be inspected completely.", 409) from error
        for current, directories, filenames in os.walk(root, followlinks=False, onerror=scan_error):
            for directory in list(directories):
                path = Path(current) / directory
                if _link(path.lstat()):
                    raise StoreError("Project contains a linked directory.", 409)
                if path == root / ".git":
                    directories.remove(directory)
                elif directory.startswith("."):
                    raise StoreError("Project contains a hidden/configuration directory.", 409)
            for filename in filenames:
                path = Path(current) / filename
                name = path.relative_to(root).as_posix()
                validate_path(name)
                if name.casefold() in seen:
                    raise StoreError("Case-colliding project files.", 409)
                seen.add(name.casefold())
                data = self._read(path)
                total += len(data)
                if total > MAX_TOTAL or len(result) >= MAX_FILES:
                    raise StoreError("Project exceeds the size or file count limit.", 413)
                result[name] = data
        return dict(sorted(result.items()))

    def _manifest(self, project_id: str) -> dict[str, dict]:
        """Content manifest for compiler/evidence consumers; no source bytes."""
        with self._transaction() as db:
            self._project(db, project_id)
            return {name: {"sha256": _hash(data), "size": len(data)}
                    for name, data in self._scan(project_id).items()}

    @staticmethod
    def _revision(files: dict[str, bytes]) -> str:
        return _hash(_json({name: _hash(data) for name, data in sorted(files.items())}).encode())

    def _info(self, row, files=None) -> dict:
        value = dict(row)
        value["revision"] = self._revision(self._scan(row["id"]) if files is None else files)
        value["state_revision"] = self._state_revision(value["revision"], value["main_file"], value["generation"])
        return value

    @staticmethod
    def _state_revision(revision: str, main_file: str, generation: int) -> str:
        return _hash(_json({"revision": revision, "main_file": main_file, "generation": generation}).encode())

    def capture_project(self, project_id: str) -> dict:
        """Capture source bytes, metadata and identity under one managed lock.

        All derived text and hashes refer to this one scan. The snapshot ID names
        this capture; state_revision also detects managed edits reverted to the
        same source bytes. External same-user filesystem mutation is not an OS
        isolation boundary, as documented above.
        """
        with self._transaction() as db:
            row = self._project(db, project_id)
            files = self._scan(project_id)
            project = self._info(row, files)
            text_files = {}
            for path, data in files.items():
                if Path(path).suffix.lower() in TEXT_EXTENSIONS:
                    try:
                        text_files[path] = data.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        continue
            return {"project": project, "files": files, "text_files": text_files,
                    "source_hashes": {name: _hash(data) for name, data in files.items()},
                    "revision": project["revision"], "state_revision": project["state_revision"],
                    "snapshot_id": uuid.uuid4().hex, "main_file": project["main_file"]}

    def list_projects(self) -> list[dict]:
        with self._transaction() as db:
            return [self._info(row) for row in db.execute("SELECT * FROM projects ORDER BY updated_at DESC")]

    def get_project(self, project_id: str) -> dict:
        with self._transaction() as db:
            return self._info(self._project(db, project_id))

    @staticmethod
    def _name(name: str) -> str:
        if not isinstance(name, str) or not name.strip() or len(name) > 160 or any(ord(c) < 32 for c in name):
            raise StoreError("Project name must contain 1 to 160 visible characters.")
        return name.strip()

    def create_project(self, name: str, template: bool = True) -> dict:
        name = self._name(name)
        with self._transaction() as db:
            return self._create_project(db, name, template)

    def _create_project(self, db, name: str, template: bool) -> dict:
        project_id = uuid.uuid4().hex
        now = time.time()
        directory = self.projects_root / project_id
        directory.mkdir()
        self._pending_projects[db].append(directory)
        db.execute("INSERT INTO projects (id,name,main_file,created_at,updated_at) VALUES (?,?,?,?,?)",
                   (project_id, name, "main.tex", now, now))
        if template:
            (directory / "main.tex").write_text(
                "\\documentclass{article}\n\\usepackage{amsmath,amssymb}\n"
                "\\title{A New Research Project}\n\\author{}\n\\date{\\today}\n"
                "\\begin{document}\n\\maketitle\n\\begin{abstract}\n"
                "Describe the research question and principal result.\n\\end{abstract}\n"
                "\\section{Introduction}\\label{sec:introduction}\n"
                "Start your research here.\n\\bibliographystyle{plain}\n"
                "\\bibliography{references}\n\\end{document}\n", encoding="utf-8")
            (directory / "references.bib").write_text("% Add verified BibTeX entries here.\n", encoding="utf-8")
        self._git(project_id, ["init", "-q"])
        self._snapshot(db, project_id, "Create project")
        return self._info(self._project(db, project_id))

    def files(self, project_id: str) -> list[dict]:
        with self._transaction() as db:
            self._project(db, project_id)
            return [{"path": name, "size": len(data), "sha256": _hash(data),
                     "text": Path(name).suffix.lower() in TEXT_EXTENSIONS}
                    for name, data in self._scan(project_id).items()]

    def read_file(self, project_id: str, path: str) -> dict:
        with self._transaction() as db:
            self._project(db, project_id)
            data = self._read(self._path(project_id, path))
            if Path(path).suffix.lower() not in TEXT_EXTENSIONS:
                raise StoreError("This is a binary asset; use asset download.", 415)
            try:
                content = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise StoreError("Text file must use UTF-8.", 415) from exc
            return {"path": path, "content": content, "sha256": _hash(data)}

    def read_bytes(self, project_id: str, path: str) -> bytes:
        with self._transaction() as db:
            self._project(db, project_id)
            return self._read(self._path(project_id, path))

    def text_files(self, project_id: str) -> dict[str, str]:
        with self._transaction() as db:
            self._project(db, project_id)
            result = {}
            for path, data in self._scan(project_id).items():
                if Path(path).suffix.lower() in TEXT_EXTENSIONS:
                    try:
                        result[path] = data.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        continue
            return result

    def _atomic(self, path: Path, data: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / (".lemfolio-" + uuid.uuid4().hex)
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _journal(self, project_id: str, changes: dict[str, bytes | None]) -> tuple[str, Path]:
        tx_id = uuid.uuid4().hex
        directory = self.journal_root / tx_id
        directory.mkdir()
        before = {}
        for i, name in enumerate(changes):
            path = self._path(project_id, name)
            before[name] = str(i) if path.exists() else None
            if path.exists():
                self._atomic(directory / str(i), self._read(path))
        self._atomic(directory / "journal.json", _json({"project_id": project_id, "before": before}).encode())
        return tx_id, directory

    def _rollback(self, directory: Path):
        record = json.loads(self._read(directory / "journal.json"))
        project_id = record["project_id"]
        if not _ID.fullmatch(project_id):
            raise StoreError("Invalid recovery journal.", 409)
        for name, backup in record["before"].items():
            target = self._path(project_id, name)
            if backup is None:
                target.unlink(missing_ok=True)
            elif isinstance(backup, str) and backup.isdigit():
                self._atomic(target, self._read(directory / backup))
            else:
                raise StoreError("Invalid recovery journal backup.", 409)
        if "git_before" in record:
            saved = record["git_before"]
            head, reference = saved["head"], saved["reference"]
            if head is not None and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
                raise StoreError("Invalid recovery Git commit.", 409)
            if reference is not None:
                if not isinstance(reference, str) or not reference.startswith("refs/"):
                    raise StoreError("Invalid recovery Git reference.", 409)
                self._git(project_id, ["check-ref-format", reference])
                self._git(project_id, ["update-ref", reference, head] if head is not None
                          else ["update-ref", "-d", reference])
                self._git(project_id, ["symbolic-ref", "HEAD", reference])
            elif head is not None:
                self._git(project_id, ["update-ref", "--no-deref", "HEAD", head])
            else:
                raise StoreError("Invalid recovery Git HEAD.", 409)
            # The index may contain a previously staged user edit. Restore its
            # exact bytes; resetting it to HEAD would discard that private state.
            # Updating refs and the index never writes any working-tree file.
            index = self.projects_root / project_id / ".git" / "index"
            if saved["index"]:
                self._atomic(index, self._read(directory / "git-index"))
            else:
                index.unlink(missing_ok=True)

    def _checkpoint_git(self, db, project_id: str):
        """Journal private Git state once per outer SQL transaction/project."""
        if project_id in self._git_journals[db]:
            return
        head = self._git(project_id, ["rev-parse", "--verify", "--quiet", "HEAD"],
                         allowed_returncodes=(1,)).decode().strip() or None
        reference = self._git(project_id, ["symbolic-ref", "-q", "HEAD"],
                              allowed_returncodes=(1,)).decode().strip() or None
        tx_id, directory = self._journal(project_id, {})
        self._pending_journals[db].append(directory)
        index = self.projects_root / project_id / ".git" / "index"
        has_index = index.exists()
        if has_index:
            self._atomic(directory / "git-index", self._read(index))
        self._atomic(directory / "journal.json", _json({"project_id": project_id, "before": {},
                     "git_before": {"head": head, "reference": reference, "index": has_index}}).encode())
        db.execute("INSERT INTO transactions VALUES (?)", (tx_id,))
        self._git_journals[db][project_id] = directory

    def _remove_tree(self, directory: Path):
        """Remove only a verified managed tree, including Windows Git objects."""
        directory = directory.absolute()
        if directory.parent not in {self.projects_root, self.journal_root} or not _ID.fullmatch(directory.name):
            raise StoreError("Cleanup target is outside managed storage.", 409)
        if _link(directory.lstat()):
            raise StoreError("Cleanup target must not be a link.", 409)

        def writable_retry(operation, name, error):
            target = Path(name).absolute()
            if (not target.is_relative_to(directory) or operation not in {os.unlink, os.rmdir}
                    or not isinstance(error[1], PermissionError) or _link(target.lstat())):
                raise error[1]
            # Git writes read-only loose objects on Windows. Clear that flag
            # only within the exact project being rolled back, then retry once.
            os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
            operation(name)

        shutil.rmtree(directory, onerror=writable_retry)

    def _recover(self, db):
        for directory in self.journal_root.iterdir():
            if _link(directory.lstat()) or not directory.is_dir() or not _ID.fullmatch(directory.name):
                raise StoreError("Unsafe transaction journal.", 409)
            if (directory / "journal.json").exists():
                if not db.execute("SELECT 1 FROM transactions WHERE id=?", (directory.name,)).fetchone():
                    self._rollback(directory)
            self._remove_tree(directory)
        db.execute("DELETE FROM transactions")

    def _apply(self, db, project_id: str, changes: dict[str, bytes | None], message: str,
               *, main_file: str | None = None) -> dict:
        files = self._scan(project_id)
        projected = files | {p: b for p, b in changes.items() if b is not None}
        for name, value in changes.items():
            self._path(project_id, name)
            if value is None:
                projected.pop(name, None)
        if len({p.casefold() for p in projected}) != len(projected):
            raise StoreError("Case-colliding paths.", 409)
        for path in projected:
            if any(str(parent) in projected for parent in PurePosixPath(path).parents if str(parent) != "."):
                raise StoreError("A file would be used as a directory.", 409)
        if len(projected) > MAX_FILES or sum(map(len, projected.values())) > MAX_TOTAL:
            raise StoreError("Project exceeds size limits.", 413)
        row = self._project(db, project_id)
        effective_main = main_file if main_file is not None else row["main_file"]
        if (main_file is not None or row["main_file"] in files) and effective_main not in projected:
            raise StoreError("The active main source cannot be deleted; select another main source first.", 409)
        tx_id, directory = self._journal(project_id, changes)
        self._pending_journals[db].append(directory)
        try:
            for name, value in changes.items():
                path = self._path(project_id, name)
                if value is None:
                    path.unlink(missing_ok=True)
                else:
                    self._atomic(path, value)
            if self._revision(self._scan(project_id)) != self._revision(projected):
                raise StoreError("Project changed during the transaction.", 409)
            db.execute("UPDATE projects SET updated_at=?,main_file=?,generation=generation+1 WHERE id=?",
                       (time.time(), effective_main, project_id))
            result = self._snapshot(db, project_id, message)
            db.execute("INSERT INTO transactions VALUES (?)", (tx_id,))
            # The journal remains until the next managed transaction, so the DB
            # commit is the sole authority after a process dies at this boundary.
            return result
        except BaseException:
            self._rollback(directory)
            self._remove_tree(directory)
            raise

    def write_file(self, project_id: str, path: str, content: str, expected_hash: str | None = None) -> dict:
        validate_path(path)
        data = self._text_bytes(path, content)
        with self._transaction() as db:
            self._recover(db)
            self._project(db, project_id)
            target = self._path(project_id, path)
            current = _hash(self._read(target)) if target.exists() else None
            if expected_hash != current:
                raise StoreError("File changed or overwrite hash is missing; reload before saving.", 409)
            result = self._apply(db, project_id, {path: data}, "Edit " + path)
            return {"path": path, "content": content, "sha256": _hash(data), **result}

    def upload_asset(self, project_id: str, path: str, data: bytes, expected_hash: str | None = None) -> dict:
        validate_path(path)
        if Path(path).suffix.lower() not in ASSET_EXTENSIONS or not isinstance(data, bytes):
            raise StoreError("Asset uploads support images, PDFs and fonts only.", 415)
        if len(data) > MAX_FILE:
            raise StoreError("Asset exceeds the upload size limit.", 413)
        with self._transaction() as db:
            self._recover(db)
            self._project(db, project_id)
            target = self._path(project_id, path)
            current = _hash(self._read(target)) if target.exists() else None
            if expected_hash != current:
                raise StoreError("Asset changed or overwrite hash is missing.", 409)
            result = self._apply(db, project_id, {path: data}, "Upload " + path)
            return {"path": path, "size": len(data), "sha256": _hash(data), **result}

    @staticmethod
    def _text_bytes(path: str, content: str) -> bytes:
        if Path(path).suffix.lower() not in TEXT_EXTENSIONS or not isinstance(content, str) or "\x00" in content:
            raise StoreError("Editing supports UTF-8 paper text only.", 415)
        data = content.encode("utf-8")
        if len(data) > MAX_TEXT:
            raise StoreError("Text exceeds the edit size limit.", 413)
        return data

    def delete_file(self, project_id: str, path: str, expected_hash: str | None) -> dict:
        with self._transaction() as db:
            self._recover(db)
            self._project(db, project_id)
            if expected_hash != _hash(self._read(self._path(project_id, path))):
                raise StoreError("File changed or delete hash is missing.", 409)
            return self._apply(db, project_id, {path: None}, "Delete " + path)

    def _git(self, project_id: str, args: list[str], *, allowed_returncodes: tuple[int, ...] = ()) -> bytes:
        directory = self.projects_root / project_id
        git_directory = directory / ".git"
        if git_directory.exists() and (_link(git_directory.lstat()) or not git_directory.is_dir()):
            raise StoreError("Unsafe Git directory.", 409)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_TERMINAL_PROMPT": "0"})
        try:
            result = subprocess.run(["git", "--literal-pathspecs", "-c", "core.hooksPath=" + os.devnull,
                                     "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
                                     "-c", "user.name=Lemfolio", "-c", "user.email=local@lemfolio.invalid",
                                     *args], cwd=directory, env=env, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StoreError("Git is unavailable or timed out.", 503) from exc
        if result.returncode and result.returncode not in allowed_returncodes:
            raise StoreError("Git operation failed: " + result.stderr.decode("utf-8", "replace")[:400], 500)
        return result.stdout

    def _snapshot(self, db, project_id: str, message: str) -> dict:
        message = str(message).strip()[:500] or "Snapshot"
        revision = self._revision(self._scan(project_id))
        row = self._project(db, project_id)
        self._checkpoint_git(db, project_id)
        self._git(project_id, ["add", "--all", "--", "."])
        self._git(project_id, ["commit", "--allow-empty", "-q", "-m", message])
        commit = self._git(project_id, ["rev-parse", "HEAD"]).decode().strip()
        value = {"id": uuid.uuid4().hex, "project_id": project_id, "revision": revision,
                 "commit": commit, "message": message, "created_at": time.time(),
                 "main_file": row["main_file"], "generation": row["generation"],
                 "state_revision": self._state_revision(revision, row["main_file"], row["generation"])}
        db.execute("INSERT INTO snapshots (id,project_id,revision,commit_hash,message,created_at,main_file,generation,state_revision) "
                   "VALUES (?,?,?,?,?,?,?,?,?)", tuple(value.values()))
        return value

    def snapshot(self, project_id: str, message: str) -> dict:
        with self._transaction() as db:
            self._recover(db)
            self._project(db, project_id)
            return self._snapshot(db, project_id, message)

    def history(self, project_id: str) -> list[dict]:
        with self._transaction() as db:
            self._project(db, project_id)
            return [{**dict(row), "commit": row["commit_hash"]} for row in db.execute(
                "SELECT * FROM snapshots WHERE project_id=? ORDER BY created_at DESC LIMIT 500", (project_id,))]

    def restore(self, project_id: str, revision: str, base_revision: str,
                *, base_state_revision: str | None = None) -> dict:
        with self._transaction() as db:
            self._recover(db)
            row = self._project(db, project_id)
            files = self._scan(project_id)
            if self._revision(files) != base_revision:
                raise StoreError("Project changed before restore.", 409)
            if base_state_revision is not None and base_state_revision != self._state_revision(
                    base_revision, row["main_file"], row["generation"]):
                raise StoreError("Project state changed before restore.", 409)
            saved = db.execute("SELECT * FROM snapshots WHERE project_id=? AND (revision=? OR commit_hash=? OR state_revision=?) "
                               "ORDER BY created_at DESC LIMIT 1", (project_id, revision, revision, revision)).fetchone()
            if not saved:
                raise StoreError("Snapshot not found.", 404)
            commit = saved["commit_hash"]
            restored = {}
            for record in self._git(project_id, ["ls-tree", "-rz", "--full-tree", commit]).split(b"\0"):
                if not record:
                    continue
                metadata, encoded_path = record.split(b"\t", 1)
                mode, kind, object_id = metadata.split()
                path = encoded_path.decode("utf-8")
                validate_path(path)
                if kind != b"blob" or mode not in {b"100644", b"100755"}:
                    raise StoreError("Snapshot contains unsupported linked files.", 409)
                data = self._git(project_id, ["cat-file", "blob", object_id.decode()])
                if len(data) > MAX_FILE:
                    raise StoreError("Snapshot file exceeds size limit.", 413)
                restored[path] = data
            if self._revision(restored) != saved["revision"]:
                raise StoreError("Snapshot content does not match its recorded revision.", 409)
            self._snapshot(db, project_id, "Before restore")
            changes = {name: restored.get(name) for name in files.keys() | restored.keys()}
            main = saved["main_file"]
            if main is None:  # Snapshots made before main-file metadata existed.
                main = "main.tex" if "main.tex" in restored else next(
                    (name for name in restored if name.lower().endswith(".tex")), None)
            if main is None or main not in restored:
                raise StoreError("Snapshot has no valid main source.", 409)
            return self._apply(db, project_id, changes, "Restore " + saved["revision"][:12], main_file=main)

    def propose(self, project_id: str, changes: list[dict], base_revision: str, summary: str,
                *, base_state_revision: str | None = None) -> dict:
        if not isinstance(changes, list) or not changes or len(changes) > 100:
            raise StoreError("A proposal must contain 1 to 100 file changes.")
        with self._transaction() as db:
            self._recover(db)
            row = self._project(db, project_id)
            files = self._scan(project_id)
            if self._revision(files) != base_revision:
                raise StoreError("Project changed before proposal creation.", 409)
            state_revision = self._state_revision(base_revision, row["main_file"], row["generation"])
            if base_state_revision is not None and base_state_revision != state_revision:
                raise StoreError("Project state changed before proposal creation.", 409)
            normalized = []
            diff = []
            seen = set()
            for change in changes:
                if not isinstance(change, dict) or set(change) - {"path", "content"} or "content" not in change:
                    raise StoreError("Invalid proposed change.")
                path = validate_path(change.get("path"))
                self._path(project_id, path)
                if path.casefold() in seen:
                    raise StoreError("Duplicate proposed paths.")
                seen.add(path.casefold())
                content = change["content"]
                if content is not None:
                    self._text_bytes(path, content)
                if path in files and Path(path).suffix.lower() not in TEXT_EXTENSIONS:
                    raise StoreError("AI proposals cannot modify binary assets.", 415)
                if content is None and path not in files:
                    raise StoreError("Cannot delete a nonexistent file.", 404)
                if content is None and path == row["main_file"]:
                    raise StoreError("The active main source cannot be deleted; select another main source first.", 409)
                before = files.get(path, b"").decode("utf-8-sig")
                for line in difflib.unified_diff(before.splitlines(keepends=True),
                                                 (content or "").splitlines(keepends=True),
                                                 fromfile="a/" + path, tofile="b/" + path):
                    diff.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
                normalized.append({"path": path, "content": content})
            proposal_id = uuid.uuid4().hex
            payload = {"id": proposal_id, "project_id": project_id, "base_revision": base_revision,
                       "base_state_revision": state_revision, "main_file": row["main_file"],
                       "summary": str(summary)[:2000], "changes": normalized, "diff": "".join(diff)}
            encoded = _json(payload)
            digest = _hash(encoded.encode())
            created_at = time.time()
            db.execute("INSERT INTO proposals (id,project_id,payload,digest,status,created_at,decided_at,result_revision) "
                       "VALUES (?,?,?,?,?,?,NULL,NULL)",
                       (proposal_id, project_id, encoded, digest, "pending", created_at))
            return {**payload, "digest": digest, "status": "pending", "created_at": created_at}

    def _proposal(self, db, project_id: str, proposal_id: str) -> dict:
        row = db.execute("SELECT * FROM proposals WHERE id=? AND project_id=?", (proposal_id, project_id)).fetchone()
        if row is None:
            raise StoreError("Proposal not found.", 404)
        if _hash(row["payload"].encode()) != row["digest"]:
            raise StoreError("Proposal integrity check failed.", 409)
        return {**json.loads(row["payload"]), **{key: row[key] for key in
                ("digest", "status", "created_at", "decided_at", "result_revision", "result_state_revision")}}

    def get_proposal(self, project_id: str, proposal_id: str) -> dict:
        with self._transaction() as db:
            self._project(db, project_id)
            return self._proposal(db, project_id, proposal_id)

    def proposals(self, project_id: str) -> list[dict]:
        with self._transaction() as db:
            self._project(db, project_id)
            ids = db.execute("SELECT id FROM proposals WHERE project_id=? ORDER BY created_at DESC LIMIT 500",
                             (project_id,)).fetchall()
            return [self._proposal(db, project_id, row["id"]) for row in ids]

    @staticmethod
    def _base_text(proposal_text: str, hunks: str) -> str | None:
        """The text a proposal was generated from, recovered by undoing its own stored diff."""
        new = proposal_text.splitlines(keepends=True)
        base, cursor = [], 0
        for line in hunks.splitlines(keepends=True):
            if line.startswith("@@"):
                match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
                if match is None:
                    return None
                start = int(match.group(1)) - (0 if match.group(2) == "0" else 1)
                base += new[cursor:start]
                cursor = start
            elif line.startswith("--- ") or line.startswith("+++ ") or line.startswith("\\"):
                continue
            elif line.startswith((" ", "-")):
                base.append(line[1:])
                cursor += line.startswith(" ")
            elif line.startswith("+"):
                cursor += 1
        base += new[cursor:]
        return "".join(base)

    @staticmethod
    def _unified(path: str, before: str, after: str) -> str:
        return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                       for line in difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                                        fromfile="a/" + path, tofile="b/" + path))

    def proposal_conflict(self, project_id: str, proposal_id: str) -> dict:
        """What moved since a proposal was generated, for the proposal's own files, from stored bytes only."""
        with self._transaction() as db:
            self._recover(db)
            row = self._project(db, project_id)
            proposal = self._proposal(db, project_id, proposal_id)
            current = self._scan(project_id)
            revision = self._revision(current)
            state_revision = self._state_revision(revision, row["main_file"], row["generation"])
            sections = re.split(r"(?m)^(?=--- a/)", proposal["diff"])
            files = []
            for change in proposal["changes"]:
                path = change["path"]
                section = next((item for item in sections if item.startswith(f"--- a/{path}\n")), "")
                recovered = None if change["content"] is None else self._base_text(change["content"], section)
                # The recovered text must reproduce the stored diff, or nothing is claimed about it.
                if recovered is None or self._unified(path, recovered, change["content"]) != section:
                    files.append({"path": path, "changed": True, "diff": None})
                    continue
                drift = self._unified(path, recovered, current.get(path, b"").decode("utf-8-sig", "replace"))
                files.append({"path": path, "changed": bool(drift), "diff": drift or None})
            stale = proposal["status"] == "pending" and (revision != proposal["base_revision"]
                                                         or state_revision != proposal.get("base_state_revision"))
            return {"proposal_id": proposal_id, "stale": stale, "base_revision": proposal["base_revision"],
                    "base_state_revision": proposal.get("base_state_revision"), "current_revision": revision,
                    "current_state_revision": state_revision, "files": files,
                    "changes_elsewhere": stale and not any(item["changed"] for item in files)}

    def decide(self, project_id: str, proposal_id: str, decision: str, digest: str) -> dict:
        if decision not in {"accept", "reject"}:
            raise StoreError("Decision must be accept or reject.")
        with self._transaction() as db:
            self._recover(db)
            row = self._project(db, project_id)
            proposal = self._proposal(db, project_id, proposal_id)
            if not isinstance(digest, str) or digest != proposal["digest"]:
                raise StoreError("Approval does not bind the displayed proposal digest.", 409)
            desired = "accepted" if decision == "accept" else "rejected"
            if proposal["status"] == desired:
                return proposal
            if proposal["status"] != "pending":
                raise StoreError("Proposal already decided.", 409)
            revision = self._revision(self._scan(project_id))
            state_revision = self._state_revision(revision, row["main_file"], row["generation"])
            if decision == "accept":
                if revision != proposal["base_revision"] or state_revision != proposal.get("base_state_revision"):
                    raise StoreError("Proposal is stale; generate a new proposal for the current project.", 409)
                changes = {item["path"]: None if item["content"] is None else
                           self._text_bytes(item["path"], item["content"]) for item in proposal["changes"]}
                applied = self._apply(db, project_id, changes, "Accept proposal: " + proposal["summary"])
                revision, state_revision = applied["revision"], applied["state_revision"]
            db.execute("UPDATE proposals SET status=?,decided_at=?,result_revision=?,result_state_revision=? WHERE id=?",
                       (desired, time.time(), revision, state_revision, proposal_id))
            return self._proposal(db, project_id, proposal_id)

    @staticmethod
    def _read_archive(data: bytes) -> tuple[dict[str, bytes], list[str], list[str]]:
        """Every paper file in a ZIP, the entries left out, and any latexmkrc text.

        latexmkrc is Perl that latexmk would execute, so it is read to find the root document and never stored."""
        if len(data) > MAX_TOTAL:
            raise StoreError("ZIP exceeds the import size limit.", 413)
        files: dict[str, bytes] = {}
        skipped: list[str] = []
        rc: list[str] = []
        total = 0
        seen = set()
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for entry in archive.infolist():
                    if entry.is_dir():
                        continue
                    filename = _archive_name(entry.filename)
                    if filename is None:
                        skipped.append(entry.filename)
                        if PurePosixPath(entry.filename).name.lower() in {"latexmkrc", ".latexmkrc"} and entry.file_size <= 64 * 1024:
                            with archive.open(entry) as stream:
                                rc.append(stream.read(64 * 1024).decode("utf-8", "ignore"))
                        continue
                    path = validate_path(filename)
                    mode = (entry.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode) or entry.flag_bits & 1:
                        raise StoreError("Linked or encrypted ZIP entries are not supported.")
                    if path.casefold() in seen:
                        raise StoreError("Duplicate or case-colliding ZIP entries.")
                    seen.add(path.casefold())
                    total += entry.file_size
                    if entry.file_size > MAX_FILE or total > MAX_TOTAL or len(files) >= MAX_FILES:
                        raise StoreError("Expanded ZIP exceeds import limits.", 413)
                    with archive.open(entry) as stream:
                        content = stream.read(MAX_FILE + 1)
                    if len(content) != entry.file_size or len(content) > MAX_FILE:
                        raise StoreError("Invalid or oversized ZIP entry.", 413)
                    files[path] = content
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise StoreError("Cannot read this ZIP archive.") from exc
        return files, skipped, rc

    def _import_files(self, db, name: str, files: dict[str, bytes], rc: list[str] = ()) -> dict:
        if not files or not any(Path(p).suffix.lower() == ".tex" for p in files):
            raise StoreError("A paper project ZIP must include at least one .tex file.")
        for path in files:
            if any(str(p) in files for p in PurePosixPath(path).parents):
                raise StoreError("ZIP contains conflicting file/directory paths.")
        project = self._create_project(db, name, template=False)
        self._apply(db, project["id"], files, "Import ZIP", main_file=_root_document(files, rc))
        return self._info(self._project(db, project["id"]))

    def import_zip(self, name: str, data: bytes) -> dict:
        name = self._name(name)
        files, _, rc = self._read_archive(data)
        files, folder = _strip_common_root(files)
        with self._transaction() as db:
            return self._import_files(db, self._name(folder) if folder and name == _DEFAULT_IMPORT else name, files, rc)

    def import_archive(self, name: str, data: bytes) -> dict:
        """Import a paper ZIP or an export holding several projects (Prism's "Export all projects").

        One failed project leaves nothing behind: the whole archive is one transaction."""
        name = self._name(name)
        files, skipped, rc = self._read_archive(data)
        export_errors = [path for path in skipped if PurePosixPath(path).name == "export-errors.json"]
        files, folder = _strip_common_root(files)
        groups = _project_groups(files)
        with self._transaction() as db:
            if groups:
                projects = [self._import_files(db, self._name(group), content, rc) for group, content in groups.items()]
            else:
                projects = [self._import_files(db, self._name(folder) if folder and name == _DEFAULT_IMPORT else name, files, rc)]
        return {"projects": projects, "skipped": skipped, "export_errors": export_errors}

    def export_zip(self, project_id: str) -> bytes:
        with self._transaction() as db:
            self._project(db, project_id)
            result = io.BytesIO()
            with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path, data in self._scan(project_id).items():
                    archive.writestr(path, data)
            return result.getvalue()

    def set_main_file(self, project_id: str, path: str, base_revision: str,
                      *, base_state_revision: str | None = None) -> dict:
        validate_path(path)
        if not path.lower().endswith(".tex"):
            raise StoreError("The main file must be a .tex source.")
        with self._transaction() as db:
            row = self._project(db, project_id)
            files = self._scan(project_id)
            if self._revision(files) != base_revision:
                raise StoreError("Project changed before updating its main file.", 409)
            if base_state_revision is not None and base_state_revision != self._state_revision(
                    base_revision, row["main_file"], row["generation"]):
                raise StoreError("Project state changed before updating its main file.", 409)
            if path not in files:
                raise StoreError("Main source not found.", 404)
            db.execute("UPDATE projects SET main_file=?,updated_at=?,generation=generation+1 WHERE id=?",
                       (path, time.time(), project_id))
            self._snapshot(db, project_id, "Select main source: " + path)
            return self._info(self._project(db, project_id), files)
