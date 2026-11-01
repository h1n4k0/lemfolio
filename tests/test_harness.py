from __future__ import annotations

import json
import subprocess
import urllib.error

import pytest

from lemfolio import harness as h


@pytest.fixture
def paper():
    return {
        "main.tex": r"""\documentclass{article}
\newtheorem{theorem}{Theorem}
\begin{document}
\section{Definitions}\label{sec:definitions}
\begin{definition}\label{def:smooth}A smooth function is differentiable.\end{definition}
\begin{equation}\label{eq:bound} x^2 + 2x + 1=(x+1)^2\end{equation}
\input{proof}
\section{Related work}
We use \citep[see][p. 2]{knuth} and \eqref{eq:bound}.
\bibliography{refs}
\end{document}
""",
        "proof.tex": r"""\section{Main result}
\begin{theorem}\label{thm:main}By Definition~\ref{def:smooth} and Eq.~\eqref{eq:bound}, the claim follows.\end{theorem}
""",
        "refs.bib": '@book{knuth, title={The {TeX} book with @inside text}, author="Donald Knuth", year=1984, doi={10.1000/test}}',
    }


def test_index_cross_file_dependency_graph_and_bib_nested_values(paper):
    index = h.index_project(paper)
    assert any(node["type"] == "definition" for node in index["nodes"])
    assert any(node["type"] == "equation" and "eq:bound" in node["labels"] for node in index["nodes"])
    assert {entry["key"] for entry in index["bibliography"]} == {"knuth"}
    assert index["bibliography"][0]["fields"]["title"] == "The {TeX} book with @inside text"
    dependencies = [edge for edge in index["edges"] if edge["type"] == "references"]
    assert any(edge["from"].startswith("node:proof.tex:") and edge["to"].startswith("node:main.tex:") for edge in dependencies)
    assert all(item["exists"] for item in index["includes"])
    assert h.verify_project(paper)["status"] == "passed"


def test_comments_verbatim_inline_verb_and_escaped_percent_not_indexed():
    source = r"""% \cite{bad1} \label{bad1}
\begin{verbatim}
\cite{bad2} \label{bad2}
\end{verbatim}
\verb|\cite{bad3}|
\begin{lstlisting}\ref{bad4}\end{lstlisting}
100\% correct \label{real}
\ref{real}
"""
    result = h.index_project({"a.tex": source})
    assert [item["key"] for item in result["labels"]] == ["real"]
    assert not result["citations"]
    assert len(result["references"]) == 1
    assert h.verify_project({"a.tex": source})["status"] == "passed"


def test_duplicate_and_missing_citations_are_independent_errors():
    result = h.verify_project({"a.tex": r"\label{a}\label{a}\ref{b}\cite{unknown}\input{missing}"})
    codes = {finding["code"] for finding in result["findings"]}
    assert {"duplicate_label", "unresolved_reference", "unresolved_citation", "missing_include"} <= codes
    assert result["status"] == "failed"
    assert result["mathematical_correctness"] == "not_verified"
    assert result["compilation"] == "not_run"


def test_legacy_bibitem_and_custom_environment():
    result = h.index_project({"a.tex": r"\newtheorem{claim}{Claim}\begin{claim}\label{cl:a}True.\end{claim}\cite{old}\begin{thebibliography}{9}\bibitem[Old 1991]{old}A Book\end{thebibliography}"})
    assert any(node["type"] == "claim" for node in result["nodes"])
    assert result["bibliography"][0]["key"] == "old"
    assert h.verify_project({"a.tex": r"\cite{old}\bibitem{old}Book"})["status"] == "passed"


def test_display_delimiters_are_equation_nodes():
    result = h.index_project({"a.tex": r"\[x^2\] and $$y=1$$"})
    assert len([node for node in result["nodes"] if node["type"] == "equation"]) == 2


def test_context_keeps_selection_and_dependency_provenance(paper):
    context = h.select_context(paper, "Check the main result smooth assumptions", {"path": "proof.tex", "start_line": 2})
    assert context["files"][0]["path"] == "proof.tex"
    assert context["snapshot_id"] == h.select_context(paper, "Check the main result smooth assumptions", {"path": "proof.tex", "start_line": 2})["snapshot_id"]
    assert any(identifier.startswith("node:main.tex:") for identifier in context["related_nodes"])
    assert "not instructions" in context["context_policy"]
    assert all(item["sha256"] for item in context["files"])


def test_context_budget_and_invalid_selection():
    source = "a" * 3000 + "\n" + r"\section{Target}" + "b" * 3000
    context = h.select_context({"a.tex": source}, "target", {"path": "a.tex", "start_line": 2}, max_chars=1000)
    assert context["truncated"] and context["context_chars"] <= 1000
    assert r"\section{Target}" in context["files"][0]["content"]
    with pytest.raises(ValueError):
        h.select_context({"a.tex": source}, "x", {"path": "gone.tex"})


def test_large_selected_file_does_not_starve_dependency_context():
    files = {"main.tex": r"\section{Selected}" + "\n" + "filler " * 30000 + r"\eqref{eq:other}",
             "dependency.tex": r"\begin{equation}\label{eq:other}x=1\end{equation}",
             "refs.bib": "@book{source,title={Evidence}}"}
    context = h.select_context(files, "selected equation", {"path": "main.tex", "start_line": 1}, max_chars=5000)
    assert {snippet["path"] for snippet in context["files"]} == set(files)
    assert context["context_chars"] <= 5000


def test_all_workflows_define_domain_acceptance():
    assert set(h.WORKFLOWS) == {"chat", "edit", "math", "literature", "review"}
    assert not h.WORKFLOWS["review"]["allows_changes"]
    assert h.WORKFLOWS["math"]["verification_criteria"]


def test_math_equivalence_counterexample_and_assumptions():
    same = h.math_check("(x+1)^2", "x^2+2*x+1")
    assert same["status"] == "symbolically_equivalent" and same["proof"] is False
    different = h.math_check("x", "y")
    assert different["status"] == "not_equivalent" and different["counterexample"]
    positive = h.math_check("sqrt(x^2)", "x", {"x": {"positive": True}})
    assert positive["status"] == "symbolically_equivalent"
    real = h.math_check("sqrt(x^2)", "x", {"x": {"real": True}})
    assert real["status"] == "not_equivalent"


@pytest.mark.parametrize("expression", ["__import__('os').system('echo unsafe')", "x.__class__", "[x for x in range(10)]", "open('x')", "2**(30**30)", "1/0", "sin(x, y)"])
def test_math_rejects_code_and_unbounded_expressions(expression):
    result = h.math_check(expression, "0")
    assert result["status"] == "unsupported"
    assert result["proof"] is False


def test_math_timeout_is_reported(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 15)
    monkeypatch.setattr(subprocess, "run", timeout)
    assert h.math_check("x", "x")["status"] == "timeout"


def test_identifier_validation_never_fetches_arbitrary_urls(monkeypatch):
    def forbid(*args):
        raise AssertionError("network must not be called")
    monkeypatch.setattr(h, "_fetch", forbid)
    for identifier in ("http://127.0.0.1/metadata", "https://example.com/10.1234/test", "10.1234/test?redirect=http://localhost", "not-a-doi"):
        assert h.lookup_reference(identifier)["status"] == "invalid_identifier"


def test_doi_lookup_matches_identifier_and_caches_evidence(monkeypatch):
    calls = []
    def fetch(url, accept):
        calls.append(url)
        assert url == "https://api.crossref.org/works/10.1234%2Ftest-harness"
        return json.dumps({"message": {"DOI": "10.1234/test-harness", "title": ["A paper"], "author": [{"given": "A", "family": "Author"}], "issued": {"date-parts": [[2024]]}}}).encode(), {"endpoint": url, "sha256": "bodyhash", "retrieved_at": "2026-10-30T00:00:00Z"}
    monkeypatch.setattr(h, "_fetch", fetch)
    h._cache.clear()
    result = h.lookup_reference("https://doi.org/10.1234/test-harness")
    assert result["status"] == "verified_metadata"
    assert result["provenance"]["sha256"] == "bodyhash"
    assert result["claim_support_verified"] is False
    assert h.lookup_reference("10.1234/test-harness")["cached"] is True
    assert len(calls) == 1


def test_mismatched_doi_is_never_verified(monkeypatch):
    monkeypatch.setattr(h, "_fetch", lambda url, accept: (json.dumps({"message": {"DOI": "10.1234/other"}}).encode(), {"endpoint": url}))
    result = h.lookup_reference("10.1234/mismatch-harness")
    assert result["status"] == "identifier_mismatch"


def test_arxiv_atom_and_rate_limit(monkeypatch):
    atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2401.12345v1</id><title> A paper </title><published>2024-01-01</published><summary>Abstract</summary><author><name>A Author</name></author></entry></feed>'''
    monkeypatch.setattr(h, "_fetch", lambda url, accept: (atom, {"endpoint": url}))
    h._arxiv_last_request = 0
    result = h.lookup_reference("arXiv:2401.12345v1")
    assert result["status"] == "verified_metadata"
    assert result["metadata"]["authors"] == ["A Author"]
    assert h.lookup_reference("2401.12346")["status"] == "rate_limited"


def test_network_failure_and_404_are_distinct(monkeypatch):
    def missing(*args):
        raise urllib.error.HTTPError("https://api.crossref.org", 404, "missing", {}, None)
    monkeypatch.setattr(h, "_fetch", missing)
    assert h.lookup_reference("10.1234/missing-harness")["status"] == "not_found"
    def unavailable(*args):
        raise OSError("offline")
    monkeypatch.setattr(h, "_fetch", unavailable)
    assert h.lookup_reference("10.1234/offline-harness")["status"] == "unavailable"
    assert h.search_literature("quantum algorithm")["status"] == "unavailable"


def test_search_returns_metadata_without_claim_support(monkeypatch):
    monkeypatch.setattr(h, "_fetch", lambda url, accept: (json.dumps({"message": {"items": [{"DOI": "10.1234/search", "title": ["Quantum algorithm"], "score": 42}]}}).encode(), {"endpoint": url}))
    result = h.search_literature("quantum algorithm", 1)
    assert result["status"] == "ok" and len(result["results"]) == 1
    assert result["results"][0]["claim_support_verified"] is False
    assert h.search_literature("x")["status"] == "invalid_query"


def test_network_allowlist_and_redirect_guard():
    with pytest.raises(ValueError):
        h._fetch("https://127.0.0.1/", "application/json")
    with pytest.raises(urllib.error.HTTPError):
        h._NoRedirect().redirect_request(urllib.request.Request("https://api.crossref.org/works"), None, 302, "redirect", {}, "http://localhost/private")
