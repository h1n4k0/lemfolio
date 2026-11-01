//! What a run shows in the assistant drawer: a Claude run as its workflow graph, a Codex run as its
//! reasoning summary and diff. Everything here is read from the run and the workflow contract;
//! node positions are the only state of its own, and they are not evidence of anything.

use crate::{MONO, Palette, icon, t};
use gpui::{FontWeight, PathBuilder, Point, Rgba, canvas, div, point, prelude::*, px};
use serde_json::Value;

pub const NODE_WIDTH: f32 = 88.;
pub const NODE_HEIGHT: f32 = 48.;
const GAP_X: f32 = 12.;
const GAP_X_MAX: f32 = 64.;
const GAP_Y: f32 = 26.;
const ARROW: f32 = 6.;
const DIFF_LINES: usize = 40;

pub struct GraphNode {
    pub id: String,
    pub label: String,
    pub status: String,
    pub historical: bool,
    pub evidence: usize,
    pub details: Vec<(String, String)>,
}

pub struct Graph {
    pub nodes: Vec<GraphNode>,
    pub edges: Vec<(usize, usize)>,
    pub positions: Vec<(f32, f32)>,
    pub selected: Option<String>,
    /// Set once the author moves a node.
    pub arranged: bool,
    /// How far each node sits from its default place. Kept apart from the positions so that a
    /// narrower drawer reflows the nodes instead of piling them against its edge.
    offsets: Vec<(f32, f32)>,
    width: f32,
}

fn columns(width: f32) -> usize {
    (((width + GAP_X) / (NODE_WIDTH + GAP_X)).floor() as usize).max(1)
}

/// Rows run alternately left to right and back, so each edge joins neighbours.
fn snake(count: usize, width: f32) -> Vec<(f32, f32)> {
    let columns = columns(width);
    let gap = if columns > 1 { ((width - columns as f32 * NODE_WIDTH) / (columns - 1) as f32).min(GAP_X_MAX) } else { 0. };
    (0..count)
        .map(|index| {
            let row = index / columns;
            let column = if row % 2 == 0 { index % columns } else { columns - 1 - index % columns };
            (column as f32 * (NODE_WIDTH + gap), row as f32 * (NODE_HEIGHT + GAP_Y))
        })
        .collect()
}

fn flatten(prefix: &str, value: &Value, out: &mut Vec<(String, String)>) {
    match value {
        Value::Null => {}
        Value::Object(map) => {
            for (key, item) in map {
                let name = if prefix.is_empty() { key.clone() } else { format!("{prefix}.{key}") };
                flatten(&name, item, out);
            }
        }
        Value::Array(items) => {
            let parts: Vec<String> = items.iter().filter(|item| !item.is_object() && !item.is_array()).map(scalar).collect();
            if !parts.is_empty() {
                out.push((prefix.to_owned(), parts.join(", ")));
            }
        }
        other => out.push((prefix.to_owned(), scalar(other))),
    }
}

fn scalar(value: &Value) -> String {
    match value {
        Value::String(text) if text.len() >= 32 && text.chars().all(|c| c.is_ascii_hexdigit()) => text.chars().take(8).collect(),
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

impl Graph {
    /// A refreshed view keeps what the author arranged and selected.
    pub fn from_view(view: &Value, previous: Option<&Graph>) -> Self {
        let nodes: Vec<GraphNode> = view["nodes"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|node| {
                let mut details = Vec::new();
                flatten("", &node["details"], &mut details);
                GraphNode {
                    id: node["id"].as_str().unwrap_or("").to_owned(),
                    label: node["label"].as_str().unwrap_or("").to_owned(),
                    status: node["status"].as_str().unwrap_or("").to_owned(),
                    historical: node["historical"].as_bool().unwrap_or(false),
                    evidence: node["evidence_refs"].as_array().map_or(0, Vec::len),
                    details,
                }
            })
            .collect();
        let position = |id: &str| nodes.iter().position(|node| node.id == id);
        let edges = view["edges"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|edge| Some((position(edge["source"].as_str()?)?, position(edge["target"].as_str()?)?)))
            .collect();
        let arranged = previous.is_some_and(|graph| graph.arranged);
        let width = previous.map_or(NODE_WIDTH, |graph| graph.width);
        let mut offsets = vec![(0., 0.); nodes.len()];
        if let Some(previous) = previous {
            for (index, node) in nodes.iter().enumerate() {
                if let Some(old) = previous.nodes.iter().position(|item| item.id == node.id) {
                    offsets[index] = previous.offsets[old];
                }
            }
        }
        let selected = previous.and_then(|graph| graph.selected.clone()).filter(|id| position(id).is_some());
        let mut graph = Self { nodes, edges, positions: Vec::new(), selected, arranged, offsets, width };
        graph.fit(width);
        graph
    }

    /// The default layout follows the drawer's width, and each node keeps its offset from it.
    pub fn fit(&mut self, width: f32) {
        self.width = width;
        self.positions = snake(self.nodes.len(), width)
            .into_iter()
            .zip(&self.offsets)
            .map(|(place, offset)| self.bounded(place.0 + offset.0, place.1 + offset.1))
            .collect();
    }

    pub fn reset(&mut self) {
        self.arranged = false;
        self.offsets = vec![(0., 0.); self.nodes.len()];
        self.fit(self.width);
    }

    fn bounded(&self, x: f32, y: f32) -> (f32, f32) {
        let bottom = (self.nodes.len().div_ceil(columns(self.width)) as f32) * (NODE_HEIGHT + GAP_Y);
        (x.clamp(0., (self.width - NODE_WIDTH).max(0.)), y.clamp(0., bottom))
    }

    fn rows_height(&self) -> f32 {
        let rows = self.nodes.len().div_ceil(columns(self.width)) as f32;
        (rows * (NODE_HEIGHT + GAP_Y) - GAP_Y).max(NODE_HEIGHT)
    }

    /// Never shorter than the default layout, so moving the lowest node does not pull up what follows.
    pub fn height(&self) -> f32 {
        self.positions.iter().map(|position| position.1 + NODE_HEIGHT).fold(self.rows_height(), f32::max)
    }

    pub fn drag_to(&mut self, index: usize, x: f32, y: f32) {
        if index >= self.positions.len() {
            return;
        }
        let target = self.bounded(x, y);
        let place = snake(self.nodes.len(), self.width)[index];
        self.positions[index] = target;
        self.offsets[index] = (target.0 - place.0, target.1 - place.1);
        self.arranged = true;
    }

    pub fn has_running(&self) -> bool {
        self.nodes.iter().any(|node| node.status == "running")
    }
}

/// An edge leaves and enters on the sides facing each other, wherever the nodes have been dragged.
fn edge_curve(from: (f32, f32), to: (f32, f32)) -> [(f32, f32); 4] {
    let (dx, dy) = (to.0 - from.0, to.1 - from.1);
    if dx.abs() >= dy.abs() {
        let sign = if dx >= 0. { 1. } else { -1. };
        let start = (from.0 + if sign > 0. { NODE_WIDTH } else { 0. }, from.1 + NODE_HEIGHT / 2.);
        let end = (to.0 + if sign > 0. { 0. } else { NODE_WIDTH }, to.1 + NODE_HEIGHT / 2.);
        let bend = ((end.0 - start.0).abs() / 2.).max(12.) * sign;
        [start, (start.0 + bend, start.1), (end.0 - bend, end.1), end]
    } else {
        let sign = if dy >= 0. { 1. } else { -1. };
        let start = (from.0 + NODE_WIDTH / 2., from.1 + if sign > 0. { NODE_HEIGHT } else { 0. });
        let end = (to.0 + NODE_WIDTH / 2., to.1 + if sign > 0. { 0. } else { NODE_HEIGHT });
        let bend = ((end.1 - start.1).abs() / 2.).max(12.) * sign;
        [start, (start.0, start.1 + bend), (end.0, end.1 - bend), end]
    }
}

/// The edges under the node cards. An edge counts as reached when its source passed and its
/// target has started, so the colour repeats the statuses and adds no claim of its own.
pub fn edges_element(graph: &Graph, palette: &Palette) -> impl IntoElement {
    let (reached, waiting) = (palette.good, palette.line_strong);
    let curves: Vec<([(f32, f32); 4], Rgba)> = graph
        .edges
        .iter()
        .map(|&(source, target)| {
            let flowing = graph.nodes[source].status == "passed" && !matches!(graph.nodes[target].status.as_str(), "not_run" | "blocked");
            (edge_curve(graph.positions[source], graph.positions[target]), if flowing { reached } else { waiting })
        })
        .collect();
    canvas(
        |_, _, _| {},
        move |bounds, _, window, _| {
            let at = |(x, y): (f32, f32)| -> Point<gpui::Pixels> { bounds.origin + point(px(x), px(y)) };
            for (curve, color) in &curves {
                let mut line = PathBuilder::stroke(px(1.5));
                line.move_to(at(curve[0]));
                line.cubic_bezier_to(at(curve[3]), at(curve[1]), at(curve[2]));
                if let Ok(path) = line.build() {
                    window.paint_path(path, *color);
                }
                // The head points along the curve's last control segment.
                let (dx, dy) = (curve[3].0 - curve[2].0, curve[3].1 - curve[2].1);
                let length = (dx * dx + dy * dy).sqrt().max(1.);
                let (ux, uy) = (dx / length, dy / length);
                let tip = curve[3];
                let base = (tip.0 - ux * ARROW, tip.1 - uy * ARROW);
                let mut head = PathBuilder::fill();
                head.move_to(at(tip));
                head.line_to(at((base.0 - uy * ARROW * 0.6, base.1 + ux * ARROW * 0.6)));
                head.line_to(at((base.0 + uy * ARROW * 0.6, base.1 - ux * ARROW * 0.6)));
                head.close();
                if let Ok(path) = head.build() {
                    window.paint_path(path, *color);
                }
            }
        },
    )
    .absolute()
    .top_0()
    .left_0()
    .size_full()
}

pub fn node_label(node: &GraphNode) -> String {
    match node.id.as_str() {
        "source" => t("输入论文"),
        "context" => t("冻结上下文"),
        "tools" => t("独立科研检查"),
        "model" => t("模型建议"),
        "proposal" => t("修改提案"),
        "approval" => t("人工审批"),
        "compile" => t("隔离编译"),
        "verification" => t("科研验收"),
        _ => node.label.as_str(),
    }
    .to_owned()
}

pub fn status_label(status: &str) -> &str {
    match status {
        "not_run" => t("未运行"),
        "running" => t("运行中"),
        "pending_review" => t("待审阅"),
        "passed" => t("已通过"),
        "failed" => t("失败"),
        "interrupted" => t("已中断"),
        "blocked" => t("受阻"),
        other => other,
    }
}

pub fn status_color(status: &str, palette: &Palette) -> Rgba {
    match status {
        "passed" => palette.good,
        "running" => palette.accent,
        "failed" | "blocked" | "interrupted" | "pending_review" => palette.attention,
        _ => palette.muted,
    }
}

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum LineKind {
    Added,
    Removed,
    Gap,
}

pub struct ChangeView {
    pub path: String,
    pub whole_file: bool,
    pub deleted: bool,
    pub added: usize,
    pub removed: usize,
    pub lines: Vec<(LineKind, String)>,
    pub hidden: usize,
}

/// Lines both sides share are dropped from the ends of an edit, leaving what really moved.
fn edit_lines(old: &str, new: &str) -> Vec<(LineKind, String)> {
    let (old, new): (Vec<&str>, Vec<&str>) = (old.lines().collect(), new.lines().collect());
    let lead = old.iter().zip(&new).take_while(|(a, b)| a == b).count();
    let tail = old[lead..].iter().rev().zip(new[lead..].iter().rev()).take_while(|(a, b)| a == b).count();
    let removed = old[lead..old.len() - tail].iter().map(|line| (LineKind::Removed, (*line).to_owned()));
    let added = new[lead..new.len() - tail].iter().map(|line| (LineKind::Added, (*line).to_owned()));
    removed.chain(added).collect()
}

/// A run's proposed changes as the model returned them. Nothing has been written at this point, and
/// a whole-file replacement has no old side here; Review shows that one against the file on disk.
pub fn change_views(result: &Value) -> Vec<ChangeView> {
    result["changes"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|change| {
            let edits = change["edits"].as_array();
            let content = change["content"].as_str();
            let mut lines: Vec<(LineKind, String)> = Vec::new();
            if let Some(edits) = edits {
                for edit in edits {
                    if !lines.is_empty() {
                        lines.push((LineKind::Gap, "⋯".to_owned()));
                    }
                    lines.extend(edit_lines(edit["old_text"].as_str().unwrap_or(""), edit["new_text"].as_str().unwrap_or("")));
                }
            } else if let Some(content) = content {
                lines.extend(content.lines().map(|line| (LineKind::Added, line.to_owned())));
            }
            let count = |kind: LineKind| lines.iter().filter(|line| line.0 == kind).count();
            let (added, removed) = (count(LineKind::Added), count(LineKind::Removed));
            let hidden = lines.len().saturating_sub(DIFF_LINES);
            lines.truncate(DIFF_LINES);
            ChangeView {
                path: change["path"].as_str().unwrap_or("").to_owned(),
                whole_file: edits.is_none() && content.is_some(),
                deleted: edits.is_none() && content.is_none(),
                added,
                removed,
                lines,
                hidden,
            }
        })
        .collect()
}

pub fn diff_view(changes: &[ChangeView], palette: &Palette) -> impl IntoElement {
    let (soft, hover, ink, muted, attention, line) = (palette.accent_soft, palette.line, palette.ink, palette.muted, palette.attention, palette.line);
    div()
        .flex()
        .flex_col()
        .gap(px(6.))
        .child(div().text_size(px(11.)).text_color(muted).child(t("模型建议的修改，尚未写入文件")))
        .children(changes.iter().map(|change| {
            let note = if change.deleted {
                Some(t("删除此文件"))
            } else if change.whole_file {
                Some(t("整文件替换，生成提案后可与原文比对"))
            } else {
                None
            };
            div()
                .flex()
                .flex_col()
                .rounded(px(8.))
                .border_1()
                .border_color(line)
                .overflow_hidden()
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(6.))
                        .h(px(26.))
                        .px_2()
                        .border_b_1()
                        .border_color(line)
                        .font_family(MONO)
                        .text_size(px(11.5))
                        .text_color(ink)
                        .child(icon("icons/file-diff.svg", 13., muted))
                        .child(div().flex_1().min_w_0().overflow_hidden().whitespace_nowrap().child(change.path.clone()))
                        .child(div().flex_none().font_weight(FontWeight::MEDIUM).text_color(muted).child(format!("+{} −{}", change.added, change.removed))),
                )
                .when_some(note, |this, note| this.child(div().px_2().py(px(3.)).text_size(px(11.)).text_color(muted).child(note)))
                .child(div().flex().flex_col().py(px(3.)).font_family(MONO).text_size(px(11.5)).line_height(px(17.)).children(change.lines.iter().map(|(kind, text)| {
                    let (sign, background, color) = match kind {
                        LineKind::Added => ("+", Some(soft), ink),
                        LineKind::Removed => ("−", Some(hover), attention),
                        LineKind::Gap => ("", None, muted),
                    };
                    div()
                        .flex()
                        .gap(px(6.))
                        .px_2()
                        .when_some(background, |this, background| this.bg(background))
                        .text_color(color)
                        .child(div().flex_none().w(px(9.)).child(sign))
                        .child(div().flex_1().min_w_0().child(if text.is_empty() { " ".to_owned() } else { text.clone() }))
                })))
                .when(change.hidden > 0, |this| this.child(div().px_2().pb(px(4.)).text_size(px(11.)).text_color(muted).child(crate::t1("还有 {} 行，生成提案后在审阅页查看全部", change.hidden))))
        }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn view() -> Value {
        json!({
            "nodes": [
                {"id": "source", "label": "输入论文", "status": "passed", "historical": false, "evidence_refs": [{"kind": "k", "id": "1"}], "details": {"main_file": "main.tex", "digest": "84e613a17c23576abb89a20f4956d3a34297b452806cac52501acb40aa0979c6", "error": null, "nested": {"status": "ok"}, "list": ["a", "b"], "empty": []}},
                {"id": "model", "label": "模型建议", "status": "running", "historical": false, "evidence_refs": [], "details": {}},
                {"id": "proposal", "label": "修改提案", "status": "not_run", "historical": false, "evidence_refs": [], "details": {}}
            ],
            "edges": [{"source": "source", "target": "model"}, {"source": "model", "target": "proposal"}, {"source": "model", "target": "missing"}]
        })
    }

    fn five_nodes() -> Value {
        let nodes: Vec<Value> = (0..5).map(|index| json!({"id": format!("n{index}"), "label": "n", "status": "passed", "historical": false, "evidence_refs": [], "details": {}})).collect();
        json!({"nodes": nodes, "edges": []})
    }

    #[test]
    fn the_graph_comes_from_the_contract() {
        let graph = Graph::from_view(&view(), None);
        assert_eq!(graph.nodes.len(), 3);
        // An edge to a node that is not there is dropped rather than drawn to nowhere.
        assert_eq!(graph.edges, [(0, 1), (1, 2)]);
        assert_eq!(graph.nodes[0].evidence, 1);
        assert!(graph.has_running());
        let mut details = graph.nodes[0].details.clone();
        details.sort();
        assert_eq!(
            details,
            [
                ("digest".to_owned(), "84e613a1".to_owned()),
                ("list".to_owned(), "a, b".to_owned()),
                ("main_file".to_owned(), "main.tex".to_owned()),
                ("nested.status".to_owned(), "ok".to_owned()),
            ]
        );
    }

    #[test]
    fn the_default_layout_snakes_and_follows_the_width() {
        let mut graph = Graph::from_view(&view(), None);
        graph.fit(2. * NODE_WIDTH + GAP_X);
        assert_eq!(graph.positions[0], (0., 0.));
        assert_eq!(graph.positions[1], (NODE_WIDTH + GAP_X, 0.));
        // The second row runs back, so the third node sits under the second.
        assert_eq!(graph.positions[2], (NODE_WIDTH + GAP_X, NODE_HEIGHT + GAP_Y));
        graph.fit(NODE_WIDTH);
        assert_eq!(graph.positions[1], (0., NODE_HEIGHT + GAP_Y));
        assert_eq!(graph.height(), 3. * NODE_HEIGHT + 2. * GAP_Y);
    }

    #[test]
    fn dragging_stays_inside_and_survives_a_refresh() {
        let mut graph = Graph::from_view(&view(), None);
        graph.fit(400.);
        graph.selected = Some("model".to_owned());
        graph.drag_to(1, 9000., -50.);
        assert_eq!(graph.positions[1], (400. - NODE_WIDTH, 0.));
        graph.drag_to(1, 100., 40.);
        assert!(graph.arranged);

        let mut refreshed = Graph::from_view(&view(), Some(&graph));
        refreshed.fit(400.);
        assert_eq!(refreshed.positions[1], (100., 40.));
        assert_eq!(refreshed.selected.as_deref(), Some("model"));

        // A narrower drawer reflows the nodes rather than piling them on its edge, and the
        // arrangement comes back with the width.
        let row = NODE_HEIGHT + GAP_Y;
        let mut wide = Graph::from_view(&five_nodes(), None);
        wide.fit(1000.);
        wide.drag_to(1, wide.positions[1].0 + 10., 5.);
        let arranged = wide.positions.clone();
        wide.fit(NODE_WIDTH);
        assert_eq!(wide.positions[2], (0., 2. * row));
        assert_eq!(wide.positions[4], (0., 4. * row));
        wide.fit(1000.);
        assert_eq!(wide.positions, arranged);

        // Resetting gives the default back.
        refreshed.reset();
        assert!(!refreshed.arranged);
        assert_eq!(refreshed.positions[0], (0., 0.));
    }

    #[test]
    fn edges_leave_from_the_facing_sides() {
        let right = edge_curve((0., 0.), (200., 0.));
        assert_eq!(right[0], (NODE_WIDTH, NODE_HEIGHT / 2.));
        assert_eq!(right[3], (200., NODE_HEIGHT / 2.));
        let left = edge_curve((200., 0.), (0., 0.));
        assert_eq!(left[0], (200., NODE_HEIGHT / 2.));
        assert_eq!(left[3], (NODE_WIDTH, NODE_HEIGHT / 2.));
        let down = edge_curve((0., 0.), (0., 200.));
        assert_eq!(down[0], (NODE_WIDTH / 2., NODE_HEIGHT));
        assert_eq!(down[3], (NODE_WIDTH / 2., 200.));
    }

    #[test]
    fn an_edit_shows_only_what_moved() {
        let changes = change_views(&json!({"changes": [{"path": "main.tex", "content": null, "edits": [
            {"old_text": "same\nWe restate it\nend", "new_text": "same\nWe revisit it\nend"},
            {"old_text": "gone", "new_text": ""}
        ]}]}));
        let change = &changes[0];
        assert!(!change.whole_file && !change.deleted);
        assert_eq!((change.added, change.removed), (1, 2));
        assert_eq!(
            change.lines,
            [
                (LineKind::Removed, "We restate it".to_owned()),
                (LineKind::Added, "We revisit it".to_owned()),
                (LineKind::Gap, "⋯".to_owned()),
                (LineKind::Removed, "gone".to_owned()),
            ]
        );
    }

    #[test]
    fn whole_files_deletions_and_long_diffs_say_so() {
        let long = (0..50).map(|i| format!("line {i}")).collect::<Vec<_>>().join("\n");
        let changes = change_views(&json!({"changes": [
            {"path": "a.tex", "content": long, "edits": null},
            {"path": "b.tex", "content": null, "edits": null}
        ]}));
        assert!(changes[0].whole_file);
        assert_eq!((changes[0].added, changes[0].lines.len(), changes[0].hidden), (50, DIFF_LINES, 10));
        assert!(changes[1].deleted);
        assert!(changes[1].lines.is_empty());
    }
}
