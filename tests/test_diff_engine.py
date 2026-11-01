from lemfolio import diff_engine


def test_missing_native_binary_is_explicitly_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMFOLIO_DIFF_BINARY", str(tmp_path / "absent.exe"))
    result = diff_engine.describe_diff("--- a/main.tex\n+++ b/main.tex\n@@ -1 +1 @@\n-a\n+b\n")
    assert result["status"] == "unavailable"
    assert "Build" in result["reason"]


def test_real_keel_rust_bridge_unicode_and_body_headers():
    import pytest
    if not diff_engine.binary_path().is_file():
        pytest.skip("Native diff binary has not been built")
    result = diff_engine.describe_diff("--- a/论文.tex\n+++ b/论文.tex\n@@ -7 +12 @@\n-定义😀甲结论\n+定义😀乙结论\n")
    assert result["status"] == "available"
    assert result["engine"] == "keel-rust"
    assert result["sourceCommit"] == "ee316400899344c9c98e93e81ff7a2c63b2ff20f"
    old = result["files"][0]["lines"][1]
    new = result["files"][0]["lines"][2]
    assert old["old"] == 7 and new["new"] == 12
    assert old["changed"] == [{"start": 10, "end": 13, "start_utf16": 4, "end_utf16": 5}]
    assert result["total_lines"] == 3


def test_malformed_native_diff_is_invalid():
    import pytest
    if not diff_engine.binary_path().is_file():
        pytest.skip("Native diff binary has not been built")
    assert diff_engine.describe_diff("--- a/x.tex\n+++ b/x.tex\n@@ -1,9 +1 @@\n-a\n+b\n")["status"] == "invalid"
