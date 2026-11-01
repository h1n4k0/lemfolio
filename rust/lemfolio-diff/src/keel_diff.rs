//! Unified-diff model shared by Desktop and TUI: parse, intraline (word) changes, and a row model with collapsed
//! unchanged regions. No rendering. Keel's own product diffs today are small (proposal field diffs, artifact
//! versions); this exists to test the rendering backend against large real patches (GPUIX_PERFORMANCE.md).

use std::collections::HashSet;
use std::ops::Range;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LineKind {
    Context,
    Add,
    Del,
    Hunk,
}

#[derive(Debug, Clone)]
pub struct DLine {
    pub kind: LineKind,
    pub old: Option<u32>,
    pub new: Option<u32>,
    pub text: String,
    /// Byte ranges inside `text` that differ from the paired line (word diff).
    pub changed: Vec<Range<usize>>,
}

#[derive(Debug, Clone, Default)]
pub struct DFile {
    pub path: String,
    pub adds: u32,
    pub dels: u32,
    pub lines: Vec<DLine>,
    pub status: &'static str,
}

pub fn parse_unified(text: &str) -> Vec<DFile> {
    let mut files: Vec<DFile> = vec![];
    let (mut o, mut n) = (0u32, 0u32);
    for raw in text.lines() {
        if raw.starts_with("diff --git ") {
            let path = raw.rsplit(" b/").next().unwrap_or(raw).to_string();
            files.push(DFile { path, status: "modified", ..Default::default() });
            continue;
        }
        let Some(f) = files.last_mut() else { continue };
        if raw.starts_with("new file") {
            f.status = "added";
        } else if raw.starts_with("deleted file") {
            f.status = "deleted";
        } else if raw.starts_with("@@") {
            // @@ -a,b +c,d @@
            let nums: Vec<u32> = raw
                .split(|c: char| !c.is_ascii_digit())
                .filter(|s| !s.is_empty())
                .take(4)
                .filter_map(|s| s.parse().ok())
                .collect();
            o = *nums.first().unwrap_or(&1);
            n = *nums.get(2).or(nums.get(1)).unwrap_or(&1);
            f.lines.push(DLine { kind: LineKind::Hunk, old: None, new: None, text: raw.to_string(), changed: vec![] });
        } else if raw.starts_with("+++") || raw.starts_with("---") || raw.starts_with("index ") || raw.starts_with("similarity") || raw.starts_with("rename ") || raw.starts_with("Binary") {
            continue;
        } else if let Some(t) = raw.strip_prefix('+') {
            f.adds += 1;
            f.lines.push(DLine { kind: LineKind::Add, old: None, new: Some(n), text: t.to_string(), changed: vec![] });
            n += 1;
        } else if let Some(t) = raw.strip_prefix('-') {
            f.dels += 1;
            f.lines.push(DLine { kind: LineKind::Del, old: Some(o), new: None, text: t.to_string(), changed: vec![] });
            o += 1;
        } else if let Some(t) = raw.strip_prefix(' ') {
            f.lines.push(DLine { kind: LineKind::Context, old: Some(o), new: Some(n), text: t.to_string(), changed: vec![] });
            o += 1;
            n += 1;
        } else if raw.is_empty() {
            f.lines.push(DLine { kind: LineKind::Context, old: Some(o), new: Some(n), text: String::new(), changed: vec![] });
            o += 1;
            n += 1;
        }
    }
    for f in &mut files {
        intraline(f);
    }
    files
}

/// Pair each block of deletions with the following block of additions line by line; mark the span between the
/// common prefix and the common suffix as changed. Cheap and predictable; not a token-level LCS.
fn intraline(f: &mut DFile) {
    let mut i = 0;
    while i < f.lines.len() {
        if f.lines[i].kind != LineKind::Del {
            i += 1;
            continue;
        }
        let ds = i;
        while i < f.lines.len() && f.lines[i].kind == LineKind::Del { i += 1; }
        let as_ = i;
        while i < f.lines.len() && f.lines[i].kind == LineKind::Add { i += 1; }
        let pairs = (as_ - ds).min(i - as_);
        for k in 0..pairs {
            let (a, b) = (f.lines[ds + k].text.clone(), f.lines[as_ + k].text.clone());
            let (ra, rb) = span(&a, &b);
            if ra.len() < a.len() || rb.len() < b.len() {
                f.lines[ds + k].changed = vec![ra];
                f.lines[as_ + k].changed = vec![rb];
            }
        }
    }
}

fn span(a: &str, b: &str) -> (Range<usize>, Range<usize>) {
    let pre = a.char_indices().zip(b.chars()).take_while(|((_, x), y)| x == y).last().map(|((i, c), _)| i + c.len_utf8()).unwrap_or(0);
    let (ar, br): (Vec<(usize, char)>, Vec<(usize, char)>) = (a.char_indices().collect(), b.char_indices().collect());
    let mut sa = a.len();
    let mut sb = b.len();
    let (mut x, mut y) = (ar.len(), br.len());
    while x > 0 && y > 0 && ar[x - 1].1 == br[y - 1].1 && ar[x - 1].0 >= pre && br[y - 1].0 >= pre {
        x -= 1;
        y -= 1;
        sa = ar[x].0;
        sb = br[y].0;
    }
    (pre..sa.max(pre), pre..sb.max(pre))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DRow {
    File(usize),
    Line(usize, usize),
    /// Unchanged lines `start..end` of file `file`, hidden.
    Gap { file: usize, start: usize, end: usize },
}

pub struct DiffDoc {
    pub files: Vec<DFile>,
    pub rows: Vec<DRow>,
    pub collapsed_files: HashSet<usize>,
    pub open_gaps: HashSet<(usize, usize)>,
    pub context: usize,
    pub file_row: Vec<usize>,
}

impl DiffDoc {
    pub fn new(files: Vec<DFile>, context: usize) -> Self {
        let mut d = Self { files, rows: vec![], collapsed_files: HashSet::new(), open_gaps: HashSet::new(), context, file_row: vec![] };
        d.build();
        d
    }

    pub fn total_lines(&self) -> usize {
        self.files.iter().map(|f| f.lines.len()).sum()
    }

    pub fn build(&mut self) {
        self.rows.clear();
        self.file_row.clear();
        for (fi, f) in self.files.iter().enumerate() {
            self.file_row.push(self.rows.len());
            self.rows.push(DRow::File(fi));
            if self.collapsed_files.contains(&fi) {
                continue;
            }
            let n = f.lines.len();
            let mut i = 0;
            while i < n {
                if f.lines[i].kind != LineKind::Context {
                    self.rows.push(DRow::Line(fi, i));
                    i += 1;
                    continue;
                }
                let s = i;
                while i < n && f.lines[i].kind == LineKind::Context { i += 1; }
                let (lead, trail) = (if s == 0 { 0 } else { self.context }, if i == n { 0 } else { self.context });
                if i - s > lead + trail + 2 && !self.open_gaps.contains(&(fi, s)) {
                    for k in s..s + lead { self.rows.push(DRow::Line(fi, k)); }
                    self.rows.push(DRow::Gap { file: fi, start: s + lead, end: i - trail });
                    for k in i - trail..i { self.rows.push(DRow::Line(fi, k)); }
                } else {
                    for k in s..i { self.rows.push(DRow::Line(fi, k)); }
                }
            }
        }
    }

    pub fn toggle_row(&mut self, row: usize) {
        match self.rows.get(row).copied() {
            Some(DRow::File(f)) => {
                if !self.collapsed_files.remove(&f) { self.collapsed_files.insert(f); }
            }
            Some(DRow::Gap { file, start, .. }) => {
                // The gap key is the start of the whole unchanged run (start - leading context).
                let run_start = if start >= self.context { start - self.context } else { start };
                let key = if self.files[file].lines.get(run_start).map(|l| l.kind == LineKind::Context).unwrap_or(false) && run_start != 0 { run_start } else { start };
                self.open_gaps.insert((file, key));
                self.open_gaps.insert((file, start));
            }
            _ => return,
        }
        self.build();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn word_span_marks_middle() {
        let (a, b) = span("let x = foo(1);", "let x = bar(1);");
        assert_eq!(&"let x = foo(1);"[a], "foo");
        assert_eq!(&"let x = bar(1);"[b], "bar");
    }
    #[test]
    fn parses_and_collapses() {
        let text = "diff --git a/x.py b/x.py\n@@ -1,16 +1,16 @@\n a\n b\n c\n d\n e\n f\n g\n h\n p\n q\n r\n s\n-i = 1\n+i = 2\n j\n k\n";
        let files = parse_unified(text);
        assert_eq!(files[0].adds, 1);
        let d = DiffDoc::new(files, 3);
        assert!(d.rows.iter().any(|r| matches!(r, DRow::Gap { .. })));
    }
}
