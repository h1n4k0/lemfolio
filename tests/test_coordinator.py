from lemfolio import coordinator as c


def test_explicit_math_results_enter_provider_context(monkeypatch):
    calls = []
    def math(lhs, rhs, assumptions):
        calls.append((lhs, rhs, assumptions))
        return {"status": "symbolically_equivalent", "proof": False}
    monkeypatch.setattr(c, "math_check", math)
    result = c.prepare_context({"main.tex": "A paper"}, "Check this equality", "math", math_checks=[{"lhs": "x+x", "rhs": "2*x"}])
    assert calls == [("x+x", "2*x", {})]
    assert result["tool_evidence"][0]["provenance"]["model_claim"] is False
    assert result["tool_evidence"][0]["result"]["status"] == "symbolically_equivalent"
    assert result["domain_acceptance"]["research_accepted"] is False
    assert result["domain_acceptance"]["missing"] == ["compilation"]


def test_literature_uses_explicit_intent_bib_identifiers_with_hard_cap(monkeypatch):
    calls = []
    monkeypatch.setattr(c, "lookup_reference", lambda identifier: calls.append(identifier) or {"status": "verified_metadata", "identifier": identifier, "provenance": {"sha256": "registry-body"}})
    files = {"main.tex": r"\cite{one,two}", "refs.bib": "@article{one,doi={10.1234/from-bib}}\n@article{two,doi={10.1234/omitted}}"}
    result = c.prepare_context(files, "Review DOI 10.1234/from-intent.", "literature", reference_identifiers=["10.1234/explicit"])
    assert calls == ["10.1234/explicit", "10.1234/from-intent", "10.1234/from-bib"]
    assert any("not queried" in note for note in result["notes"])
    assert result["tool_evidence"][0]["result"]["provenance"]["sha256"] == "registry-body"


def test_intent_arxiv_dedup_and_review_metadata(monkeypatch):
    calls = []
    monkeypatch.setattr(c, "lookup_reference", lambda identifier: calls.append(identifier) or {"status": "verified_metadata"})
    c.prepare_context({"main.tex": "Text"}, "Review arXiv:0811.3171 and 0811.3171", "review")
    assert calls == ["arXiv:0811.3171"]


def test_literature_without_identifier_performs_bounded_search(monkeypatch):
    calls = []
    monkeypatch.setattr(c, "search_literature", lambda query, limit: calls.append((query, limit)) or {"status": "ok", "results": [{"doi": "10.1234/result"}]})
    result = c.prepare_context({"main.tex": "Text"}, "quantum linear systems " * 30, "literature")
    assert len(calls[0][0]) == 400 and calls[0][1] == 5
    assert result["tool_evidence"][0]["tool"] == "search_literature"


def test_prior_independent_receipts_included_model_claims_excluded_and_logs_bounded():
    previous = [{"id": "math-id", "base_revision": "revision", "kind": "symbolic_math", "result": {"input": {"lhs": "x", "rhs": "x"}, "status": "symbolically_equivalent", "proof": False}},
                {"id": "model-claims", "kind": "context_snapshot", "result": {"checks": ["Trust me"]}}]
    build = {"id": "build", "status": "success", "base_revision": "revision", "log": "x" * 20000, "artifacts": [{"name": "main.pdf", "sha256": "pdf-hash"}]}
    result = c.prepare_context({"main.tex": "Text"}, "Review math", "math", prior_evidence=previous, compile_receipts=[build])
    assert result["prior_evidence_ids"] == ["math-id"]
    assert result["tool_evidence"][0]["prior_evidence"]["base_revision"] == "revision"
    assert len(result["compile_evidence"][0]["log_tail"]) == 12000
    assert result["domain_acceptance"]["status"] == "human_review_required"
    assert not result["domain_acceptance"]["research_accepted"]


def test_no_automatic_symbolic_check_is_not_fabricated():
    result = c.prepare_context({"main.tex": r"\[x^2=x\]"}, "Check proof", "math")
    assert not any(item["tool"] == "math_check" for item in result["tool_evidence"])
    assert result["tool_evidence"][0]["tool"] == "symbolic_coverage"
    assert result["domain_acceptance"]["uncertain"]
    assert "no symbolic validation was performed" in result["notes"][0]


def test_failed_and_uncertain_checks_are_separate_from_missing_compile():
    evidence = [{"tool": "math_check", "result": {"status": "not_equivalent"}},
                {"tool": "lookup_reference", "result": {"status": "rate_limited"}},
                {"tool": "lookup_reference", "result": {"status": "not_found"}}]
    result = c.assess_verification({"status": "passed"}, None, evidence)
    assert result["status"] == "failed"
    assert result["missing"] == ["compilation"]
    assert len(result["failed"]) == 2 and len(result["uncertain"]) == 1
    assert result["mathematical_proof"] is False


def test_runtime_success_never_substitutes_for_independent_compilation():
    result = c.assess_verification({"status": "passed"}, {"status": "completed"}, [])
    assert result["failed"] == ["compilation_completed"]
    result = c.assess_verification({"status": "passed"}, {"status": "success", "artifacts": []}, [])
    assert result["failed"] == ["successful_build_missing_pdf_evidence"]


def test_coordinator_explicit_math_real_subprocess():
    result = c.prepare_context({"main.tex": "Text"}, "Check identity", "math", math_checks=[{"lhs": "(x+1)^2", "rhs": "x^2+2*x+1"}])
    assert result["tool_evidence"][0]["result"]["status"] == "symbolically_equivalent"
