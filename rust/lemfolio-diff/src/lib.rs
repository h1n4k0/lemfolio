//! Lemfolio's JSON adapter around the unchanged Keel diff model.
//!
//! Upstream: h1n4k0/keel ee316400899344c9c98e93e81ff7a2c63b2ff20f,
//! experiments/keel-desktop-gpuix/crates/keel-semantic/src/diff.rs.
//! Git blob 3fe0fc3be7c44a00bbe9a9341d24e71fd3f90d29.
pub mod keel_diff;

use keel_diff::{DRow, DiffDoc, LineKind};
use serde_json::{json, Value};

pub const SOURCE_COMMIT: &str = "ee316400899344c9c98e93e81ff7a2c63b2ff20f";
const SENTINEL: char = '\u{1}';

fn range(text: &str) -> Result<(u32, u32), String> {
    let (start, count) = text.split_once(',').unwrap_or((text, "1"));
    let start: u32 = start.parse().map_err(|_| "Invalid hunk start")?;
    let count: u32 = count.parse().map_err(|_| "Invalid hunk count")?;
    if start > 10_000_000 || count > 100_000 {
        return Err("Hunk positions exceed supported limits".into());
    }
    Ok((start, count))
}

fn source_path(path: &str) -> String {
    // A tab may delimit optional unified-diff timestamps. Spaces are valid paths.
    let path = path.split('\t').next().unwrap_or(path);
    path.strip_prefix("a/").or_else(|| path.strip_prefix("b/")).unwrap_or(path).to_string()
}

/// Normalize headers without changing the vendored implementation. Prefixing
/// every hunk body with one sentinel prevents source lines beginning with ++/--
/// from being mistaken for file headers by upstream's small-patch parser.
fn normalize(text: &str) -> Result<(String, Vec<String>, Vec<Option<&'static str>>), String> {
    let mut output = String::new();
    let mut paths = Vec::new();
    let mut statuses = Vec::new();
    let mut old_header: Option<String> = None;
    let mut marker_pending = false;
    let (mut old_left, mut new_left) = (0u32, 0u32);
    for (line_number, raw) in text.lines().enumerate() {
        if line_number > 100_000 {
            return Err("Diff exceeds 100,000 lines".into());
        }
        if raw.starts_with("\\ No newline at end of file") {
            continue;
        }
        if old_left > 0 || new_left > 0 {
            let Some(prefix) = raw.chars().next() else {
                return Err("A hunk body is missing its diff prefix".into());
            };
            match prefix {
                ' ' if old_left > 0 && new_left > 0 => { old_left -= 1; new_left -= 1; }
                '-' if old_left > 0 => old_left -= 1,
                '+' if new_left > 0 => new_left -= 1,
                _ => return Err("Hunk body does not match declared line counts".into()),
            }
            output.push(prefix);
            output.push(SENTINEL);
            output.push_str(&raw[1..]);
            output.push('\n');
            continue;
        }
        if raw.starts_with("diff --git ") {
            output.push_str(raw);
            output.push('\n');
            paths.push(raw.rsplit(" b/").next().unwrap_or(raw).to_string());
            statuses.push(None);
            old_header = None;
            marker_pending = true;
        } else if let Some(path) = raw.strip_prefix("--- ") {
            old_header = Some(source_path(path));
        } else if let Some(path) = raw.strip_prefix("+++ ") {
            let new_path = source_path(path);
            let selected = if new_path == "/dev/null" {
                old_header.clone().unwrap_or_else(|| "paper.tex".to_string())
            } else { new_path.clone() };
            if !marker_pending {
                output.push_str(&format!("diff --git a/{selected} b/{selected}\n"));
                paths.push(selected.clone());
                statuses.push(None);
            }
            if let Some(last) = paths.last_mut() { *last = selected; }
            if let Some(last) = statuses.last_mut() {
                *last = if new_path == "/dev/null" { Some("deleted") }
                    else if old_header.as_deref() == Some("/dev/null") { Some("added") }
                    else { None };
            }
            old_header = None;
            marker_pending = false;
        } else if let Some(header) = raw.strip_prefix("@@ -") {
            let (old, rest) = header.split_once(" +").ok_or("Malformed hunk header")?;
            let (new, tail) = rest.split_once(" @@").ok_or("Malformed hunk header")?;
            let (old_start, old_count) = range(old)?;
            let (new_start, new_count) = range(new)?;
            if paths.is_empty() {
                return Err("Hunk has no file header".into());
            }
            output.push_str(&format!("@@ -{old_start},{old_count} +{new_start},{new_count} @@{tail}\n"));
            old_left = old_count;
            new_left = new_count;
            marker_pending = false;
        } else if raw.starts_with("new file") || raw.starts_with("deleted file") {
            output.push_str(raw);
            output.push('\n');
        } else if raw.starts_with("index ") || raw.starts_with("similarity ")
            || raw.starts_with("rename ") || raw.starts_with("Binary ") || raw.is_empty() {
            // Metadata doesn't affect the visible source rows.
        } else {
            return Err("Unsupported diff metadata or a body outside its hunk".into());
        }
    }
    if old_left != 0 || new_left != 0 {
        return Err("Diff ended before the declared hunk body".into());
    }
    Ok((output, paths, statuses))
}

pub fn describe(unified: &str, context: usize) -> Value {
    if unified.len() > 4 * 1024 * 1024 {
        return json!({"status":"invalid","engine":"keel-rust","error":"Diff exceeds 4 MiB"});
    }
    let (normalized, paths, statuses) = match normalize(unified) {
        Ok(value) => value,
        Err(error) => return json!({"status":"invalid","engine":"keel-rust","error":error}),
    };
    let mut files = keel_diff::parse_unified(&normalized);
    for (index, file) in files.iter_mut().enumerate() {
        if let Some(path) = paths.get(index) { file.path = path.clone(); }
        if let Some(Some(status)) = statuses.get(index) { file.status = status; }
        for line in &mut file.lines {
            if line.text.starts_with(SENTINEL) {
                line.text.remove(0);
                for changed in &mut line.changed {
                    changed.start = changed.start.saturating_sub(1);
                    changed.end = changed.end.saturating_sub(1);
                }
            }
        }
    }
    let doc = DiffDoc::new(files, context.min(100));
    let files: Vec<Value> = doc.files.iter().map(|file| {
        let lines: Vec<Value> = file.lines.iter().map(|line| {
            let kind = match line.kind {
                LineKind::Context => "context", LineKind::Add => "add",
                LineKind::Del => "del", LineKind::Hunk => "hunk",
            };
            let changed: Vec<Value> = line.changed.iter().map(|span| json!({
                "start":span.start, "end":span.end,
                "start_utf16":line.text[..span.start].encode_utf16().count(),
                "end_utf16":line.text[..span.end].encode_utf16().count(),
            })).collect();
            json!({"kind":kind,"old":line.old,"new":line.new,"text":line.text,"changed":changed})
        }).collect();
        json!({"path":file.path,"adds":file.adds,"dels":file.dels,"status":file.status,"lines":lines})
    }).collect();
    let rows: Vec<Value> = doc.rows.iter().map(|row| match row {
        DRow::File(file) => json!({"kind":"file","file":file}),
        DRow::Line(file,line) => json!({"kind":"line","file":file,"line":line}),
        DRow::Gap {file,start,end} => json!({"kind":"gap","file":file,"start":start,"end":end}),
    }).collect();
    json!({"status":"available","engine":"keel-rust","sourceCommit":SOURCE_COMMIT,
           "range_encoding":"utf8_bytes_with_utf16_indices","files":files,"rows":rows,
           "total_lines":doc.total_lines(),"context":doc.context})
}

#[cfg(test)]
mod adapter_tests {
    use super::*;

    #[test]
    fn plain_diff_and_omitted_counts_have_correct_positions() {
        let result = describe("--- a/main.tex\n+++ b/main.tex\n@@ -7 +12,2 @@\n-before\n+after\n+extra\n",3);
        assert_eq!(result["status"], "available");
        assert_eq!(result["files"][0]["path"], "main.tex");
        assert_eq!(result["files"][0]["lines"][1]["old"], 7);
        assert_eq!(result["files"][0]["lines"][2]["new"], 12);
        assert_eq!(result["files"][0]["lines"][3]["new"], 13);
    }

    #[test]
    fn unicode_byte_spans_and_utf16_positions_are_consistent() {
        let result = describe("--- a/论文.tex\n+++ b/论文.tex\n@@ -1 +1 @@\n-定义😀甲结论\n+定义😀乙结论\n",3);
        let line = &result["files"][0]["lines"][1];
        assert_eq!(line["text"], "定义😀甲结论");
        assert_eq!(line["changed"][0]["start"], 10);
        assert_eq!(line["changed"][0]["end"], 13);
        assert_eq!(line["changed"][0]["start_utf16"], 4);
        assert_eq!(line["changed"][0]["end_utf16"], 5);
    }

    #[test]
    fn body_resembling_headers_is_not_lost() {
        let result = describe("--- a/main.tex\n+++ b/main.tex\n@@ -1 +1 @@\n--- source\n+++ source\n",3);
        assert_eq!(result["files"][0]["dels"], 1);
        assert_eq!(result["files"][0]["adds"], 1);
        assert_eq!(result["files"][0]["lines"][1]["text"], "-- source");
        assert_eq!(result["files"][0]["lines"][2]["text"], "++ source");
    }

    #[test]
    fn new_deleted_multiple_files_and_no_newline_markers() {
        let result = describe("--- /dev/null\n+++ b/新.tex\n@@ -0,0 +1 @@\n+结果\n\\ No newline at end of file\n--- a/旧.tex\n+++ /dev/null\n@@ -1 +0,0 @@\n-旧结果\n",3);
        assert_eq!(result["files"].as_array().unwrap().len(),2);
        assert_eq!(result["files"][0]["status"],"added");
        assert_eq!(result["files"][1]["status"],"deleted");
    }

    #[test]
    fn malformed_and_overflow_hunks_fail_explicitly() {
        for input in ["@@ -1 +1 @@\n-a\n+b\n", "--- a/x.tex\n+++ b/x.tex\n@@ -1,2 +1,2 @@\n-a\n+b\n",
                      "--- a/x.tex\n+++ b/x.tex\n@@ -4294967295 +1 @@\n-a\n+b\n"] {
            assert_eq!(describe(input,3)["status"],"invalid");
        }
    }
}
