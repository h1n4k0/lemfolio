"""Real Docker acceptance tests; skipped only when its engine/image is unavailable."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import time

import pytest

from lemfolio.compiler import Compiler
from lemfolio.store import Store


@pytest.fixture
def docker_workspace():
    workspace = Path(__file__).resolve().parents[1]
    base = workspace / ".test-builds"
    base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="docker-", dir=base) as temporary:
        root = Path(temporary).resolve()
        assert root.is_relative_to(workspace.resolve()), "all compiler test artifacts must stay in the project on D:"
        store = Store(root)
        compiler = Compiler(root, store, timeout=90)
        if not compiler.status()["available"]:
            pytest.skip("Docker engine or lemfolio/tex:0.1 image is unavailable")
        yield root, store, compiler
        for build_id in list(compiler.workers):
            compiler.interrupt(build_id)
        for worker in list(compiler.workers.values()):
            worker.join(timeout=20)


def _finished(compiler, build):
    deadline = time.monotonic() + 110
    while time.monotonic() < deadline:
        record = compiler.inspect(build["id"])
        if record["status"] not in {"queued", "running"}:
            return record
        time.sleep(.2)
    compiler.interrupt(build["id"])
    pytest.fail("real Docker compiler did not settle within its bounded timeout")


def test_real_docker_custom_class_style_pdf_synctex_and_snapshot_isolation(docker_workspace):
    root, store, compiler = docker_workspace
    project = store.create_project("Docker acceptance", template=False)
    pid = project["id"]
    store.write_file(pid, "customclass.cls", "\\NeedsTeXFormat{LaTeX2e}\n\\ProvidesClass{customclass}\n\\LoadClass{article}\n", None)
    store.write_file(pid, "customstyle.sty", "\\ProvidesPackage{customstyle}\n\\newcommand{\\proofword}{SnapshotA}\n", None)
    source = "\\documentclass{customclass}\n\\usepackage{customstyle}\n\\begin{document}\n\\section{Test}\\label{sec:test}\n\\proofword. Independent snapshot.\n\\end{document}\n"
    store.write_file(pid, "main.tex", source, None)
    before = store.get_project(pid)["revision"]
    build = compiler.submit(pid)
    # Editing the live project immediately after submission cannot change build input.
    main = store.read_file(pid, "main.tex")
    store.write_file(pid, "main.tex", source.replace("Independent snapshot", "New live revision"), main["sha256"])
    result = _finished(compiler, build)
    assert result["status"] == "success", result.get("log", result)
    assert result["base_revision"] == before
    assert result["source_hashes"]["main.tex"] == hashlib.sha256(source.encode()).hexdigest()
    assert store.read_file(pid, "main.tex")["content"] != source
    pdf = compiler.artifact(build["id"], "main.pdf")
    assert pdf.read_bytes().startswith(b"%PDF-")
    assert any(item["name"].endswith(".synctex.gz") for item in result["artifacts"])
    assert compiler.synctex(build["id"], "main.tex", line=5)["status"] == "matched"
    forward = compiler.synctex(build["id"], "main.tex", line=5)
    assert forward["boxes"] and forward["boxes"][0]["page"] == 1 and forward["boxes"][0]["width"] > 0
    box = forward["boxes"][0]
    inverse = compiler.synctex_inverse(build["id"], box["page"], box["x"] + 1, box["y"] + box["height"] / 2)
    assert inverse["status"] == "matched" and inverse["file"] == "main.tex" and inverse["line"] >= 1
    assert inverse["state_revision"] == build["state_revision"]
    assert compiler.synctex_inverse(build["id"], 999, 10, 10)["status"] == "no_match"
    with pytest.raises(ValueError):
        compiler.synctex_inverse(build["id"], 1, -1, 10)
    assert compiler.inspect(build["id"])["image_id"].startswith("sha256:")


def test_real_docker_shell_escape_disabled_and_invalid_tex_fails(docker_workspace):
    root, store, compiler = docker_workspace
    project = store.create_project("Untrusted source acceptance", template=False)
    pid = project["id"]
    source = "\\documentclass{article}\n\\begin{document}\n\\immediate\\write18{touch /out/escaped.txt}\nShell escape must be disabled.\n\\end{document}\n"
    store.write_file(pid, "main.tex", source, None)
    build = compiler.submit(pid)
    result = _finished(compiler, build)
    assert result["status"] == "success", result.get("log", result)
    assert not (root / "builds" / build["id"] / "out" / "escaped.txt").exists()
    main = store.read_file(pid, "main.tex")
    store.write_file(pid, "main.tex", source.replace("Shell escape must be disabled.", "\\UndefinedLemfolioCommand"), main["sha256"])
    failed = _finished(compiler, compiler.submit(pid))
    assert failed["status"] == "failed"
    assert "Undefined control sequence" in failed.get("log", "")
    assert not any(item["name"].endswith(".pdf") for item in failed["artifacts"])


def test_real_docker_default_new_project_compiles(docker_workspace):
    _, store, compiler = docker_workspace
    project = store.create_project("Default project compile")
    result = _finished(compiler, compiler.submit(project["id"]))
    assert result["status"] == "success", result.get("log", result)


def test_real_docker_source_overwrite_attempt_is_refused(docker_workspace):
    root, store, compiler = docker_workspace
    project = store.create_project("Source write refusal", template=False)
    pid = project["id"]
    victim = "Original immutable source.\n"
    store.write_file(pid, "victim.tex", victim, None)
    attack = "\\documentclass{article}\n\\newwrite\\lemwrite\n\\begin{document}\n\\immediate\\openout\\lemwrite=/src/victim.tex\n\\immediate\\write\\lemwrite{overwritten}\n\\end{document}\n"
    store.write_file(pid, "main.tex", attack, None)
    result = _finished(compiler, compiler.submit(pid))
    assert result["status"] == "failed"
    assert store.read_file(pid, "victim.tex")["content"] == victim
    assert (root / "builds" / result["id"] / "work" / "victim.tex").read_text() == victim
    assert "Not writing" in result.get("log", "") or "can't write" in result.get("log", "")


@pytest.mark.parametrize("engine", ["xelatex", "lualatex"])
def test_real_docker_additional_tex_engines(docker_workspace, engine):
    _, store, compiler = docker_workspace
    project = store.create_project(f"{engine} acceptance", template=engine == "lualatex")
    if engine == "xelatex":
        source = "\\documentclass{article}\n\\usepackage{fontspec}\n\\setmainfont{Noto Serif CJK SC}\n\\begin{document}\n中文科研工作台：从引理到论文。\\par Lemfolio engine acceptance.\n\\end{document}\n"
        store.write_file(project["id"], "main.tex", source, None)
    result = _finished(compiler, compiler.submit(project["id"], engine=engine))
    assert result["status"] == "success", result.get("log", result)
    assert result["engine"] == engine
    assert compiler.artifact(result["id"], "main.pdf").read_bytes().startswith(b"%PDF-")
    assert "XeTeX" in result.get("log", "") if engine == "xelatex" else "LuaHBTeX" in result.get("log", "")
