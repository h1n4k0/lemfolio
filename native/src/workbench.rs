//! The paper workbench, drawn by GPUI. Project state, runs, builds and evidence stay in the local
//! service, read over the same contract; this file holds only what is on screen.
//!
//! The assistant is a drawer under the centre column, so it stays open across Source, Review,
//! Workflow and Evidence: a tab above it shrinks to make room rather than being covered.

use crate::editor::{Editor, EditorEvent, EditorKind};
use crate::pdf::{self, Page};
use crate::run_view::{self, ChangeView, Graph, NODE_HEIGHT, NODE_WIDTH};
use crate::{Backend, MONO, Palette, Project, button, icon, t, t1};
use gpui::{
    Animation, AnimationExt, AnyElement, BoxShadow, Context, Div, Entity, FontWeight, Hsla, MouseButton, MouseDownEvent,
    MouseMoveEvent, MouseUpEvent, ObjectFit, Rgba, Role, ScrollHandle, SharedString, Stateful, Subscription,
    Window, deferred, div, hsla, img, linear_color_stop, linear_gradient, point, prelude::*, pulsating_between, px, svg,
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, HashMap},
    sync::{Arc, Mutex},
    time::Duration,
};

const OPEN_MS: u64 = 340;
const CLOSE_MS: u64 = 190;
const DRAWER_MIN: f32 = 200.;
const DRAWER_DEFAULT: f32 = 320.;

/// Under-damped spring from 0 to 1: it passes the target slightly and settles, like the reference.
fn spring(t: f32) -> f32 {
    1. - (-6.2 * t).exp() * (9.5 * t).cos()
}

fn ease_in(t: f32) -> f32 {
    t * t
}

fn encode(value: &str) -> String {
    let mut out = String::new();
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
            out.push(byte as char);
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

fn short(value: &str, length: usize) -> String {
    value.chars().take(length).collect()
}

fn text(value: &Value, pointer: &str) -> String {
    value.pointer(pointer).and_then(Value::as_str).unwrap_or("").to_owned()
}

#[derive(Clone, Copy, PartialEq)]
enum Tab {
    Source,
    Review,
    Workflow,
    Evidence,
}

#[derive(Clone, Copy, PartialEq)]
enum Motion {
    Hidden,
    Open,
    Closing,
}

#[derive(Clone)]
struct PaperFile {
    path: String,
    is_text: bool,
}

const MODES: &[(&str, &str, &str, &str)] = &[
    ("chat", "icons/messages-square.svg", "讨论", "就论文内容提问，不改文件"),
    ("edit", "icons/pen-line.svg", "语义修改", "说明要改什么，结果进入审阅，由你决定是否写入"),
    ("math", "icons/sigma.svg", "数学推理", "说明要检查的推导。把等式写进反引号，如 `(x+1)**2 = x**2+2*x+1`，由 SymPy 独立检查"),
    ("literature", "icons/book-open-text.svg", "文献", "可直接写 DOI 或 arXiv 编号，会核验元数据"),
    ("review", "icons/clipboard-check.svg", "论文审阅", "审阅论断、假设与证据，不改文件"),
];

struct Turn {
    id: String,
    intent: String,
    mode: String,
    provider: String,
    model: String,
    effort: String,
    status: String,
    answer: Option<String>,
    error: Option<String>,
    changes: Vec<String>,
    checks: Vec<String>,
    thinking: Vec<String>,
    thinking_open: bool,
    proposed: bool,
    suggestion: Option<String>,
    graph: Option<Graph>,
    graph_error: Option<String>,
    diffs: Vec<ChangeView>,
}

/// A node being moved: where the press landed and where the node was then.
struct NodeDrag {
    run: String,
    node: usize,
    start: (f32, f32),
    origin: (f32, f32),
    moved: bool,
}

/// One model as its provider lists it, with the effort levels the provider reports for it.
struct ProviderEntry {
    id: String,
    name: String,
    /// "subscription", "harness" or "custom", as the runtime reports it.
    kind: String,
    installed: bool,
    ready: bool,
}

struct ModelEntry {
    id: String,
    name: String,
    efforts: Vec<String>,
    default_effort: Option<String>,
}

/// The levels on offer for a selection. With no model chosen the CLI picks one this program cannot
/// name, so only levels every listed model accepts are offered.
fn efforts_for(models: &[ModelEntry], model: &str) -> (Vec<String>, Option<String>) {
    if let Some(entry) = models.iter().find(|entry| entry.id == model) {
        return (entry.efforts.clone(), entry.default_effort.clone());
    }
    let Some(first) = models.first() else { return (Vec::new(), None) };
    let common = first.efforts.iter().filter(|level| models.iter().all(|entry| entry.efforts.contains(level))).cloned().collect();
    (common, None)
}

/// A popup's body. GPUI has no backdrop blur, so what lies behind stays sharp but is dimmed by the shadow.
fn glass_surface(element: Stateful<Div>, palette: &Palette) -> Stateful<Div> {
    element
        .bg(linear_gradient(165., linear_color_stop(palette.glass_top, 0.), linear_color_stop(palette.glass_bottom, 1.)))
        .border_1()
        .border_color(palette.glass_rim)
        .shadow(vec![
            BoxShadow { color: hsla(0., 0., 0., 0.42), offset: point(px(0.), px(18.)), blur_radius: px(44.), spread_radius: px(-6.), inset: false },
            BoxShadow { color: hsla(0., 0., 0., 0.24), offset: point(px(0.), px(2.)), blur_radius: px(8.), spread_radius: px(0.), inset: false },
        ])
}

/// The light along a popup's upper edge.
fn glass_sheen(palette: &Palette) -> Div {
    div().absolute().top_0().left_0().right_0().h(px(64.)).rounded_t(px(14.)).bg(linear_gradient(180., linear_color_stop(palette.glass_sheen, 0.), linear_color_stop(Hsla::transparent_black(), 1.)))
}

/// The tint that gathers along its lower edge.
fn glass_glow(palette: &Palette) -> Div {
    div().absolute().bottom_0().left_0().right_0().h(px(56.)).rounded_b(px(14.)).bg(linear_gradient(0., linear_color_stop(palette.glass_glow, 0.), linear_color_stop(Hsla::transparent_black(), 1.)))
}

impl Turn {
    fn running(&self) -> bool {
        matches!(self.status.as_str(), "queued" | "running")
    }

    /// Claude runs are shown as their workflow; other backends as their summary and diff.
    fn shows_graph(&self) -> bool {
        self.provider == "claude"
    }

    fn apply(&mut self, run: &Value) {
        self.status = text(run, "/status");
        self.error = run["error"].as_str().map(str::to_owned);
        if let Some(result) = run.get("result").filter(|value| value.is_object()) {
            self.answer = result["answer"].as_str().map(str::to_owned);
            self.changes = result["changes"]
                .as_array()
                .map(|items| items.iter().map(|item| text(item, "/path")).collect())
                .unwrap_or_default();
            self.checks = result["checks"]
                .as_array()
                .map(|items| items.iter().filter_map(|item| item.as_str().map(str::to_owned)).collect())
                .unwrap_or_default();
            self.diffs = run_view::change_views(result);
        }
    }

    fn from_run(run: &Value) -> Self {
        let mut turn = Self {
            id: text(run, "/id"),
            intent: text(run, "/task/intent"),
            mode: text(run, "/task/mode"),
            provider: text(run, "/task/provider"),
            model: text(run, "/task/model"),
            effort: text(run, "/task/effort"),
            status: String::new(),
            answer: None,
            error: None,
            changes: Vec::new(),
            checks: Vec::new(),
            thinking: Vec::new(),
            thinking_open: true,
            proposed: false,
            suggestion: None,
            graph: None,
            graph_error: None,
            diffs: Vec::new(),
        };
        turn.apply(run);
        turn
    }
}

/// The reasoning trail from a run's events: what the model chose to report, nothing derived.
/// Provider summaries arrive whole; notes arrive as text fragments and are joined into lines.
pub fn thinking_lines(events: &[Value]) -> Vec<String> {
    let mut lines = Vec::new();
    let mut notes = String::new();
    let flush = |notes: &mut String, lines: &mut Vec<String>| {
        for line in notes.lines() {
            let line = line.trim().trim_start_matches(['-', '*', '•']).trim();
            if !line.is_empty() && !line.starts_with('{') {
                lines.push(line.to_owned());
            }
        }
        notes.clear();
    };
    for event in events {
        if event["type"] != "thinking" {
            continue;
        }
        let body = event["data"]["text"].as_str().unwrap_or("");
        if event["data"]["source"] == "provider_summary" {
            flush(&mut notes, &mut lines);
            let cleaned = body.replace("**", "");
            let cleaned = cleaned.split("\n\n").next().unwrap_or("").trim();
            if !cleaned.is_empty() {
                lines.push(cleaned.to_owned());
            }
        } else {
            notes.push_str(body);
        }
    }
    flush(&mut notes, &mut lines);
    lines
}

/// The next message the model suggested, if it suggested one. Only ever the model's own words.
pub fn suggested_reply(events: &[Value]) -> Option<String> {
    events
        .iter()
        .rev()
        .find(|event| event["type"] == "suggested_reply")
        .and_then(|event| event["data"]["text"].as_str())
        .map(str::to_owned)
}

/// Equations the author marked with backticks, handed to SymPy as a check rather than to the model.
pub fn math_checks(intent: &str) -> Vec<Value> {
    let parts: Vec<&str> = intent.split('`').collect();
    parts
        .iter()
        .skip(1)
        .step_by(2)
        .filter_map(|part| part.split_once('='))
        .filter(|(lhs, rhs)| !lhs.trim().is_empty() && !rhs.trim().is_empty() && !rhs.contains('='))
        .take(3)
        .map(|(lhs, rhs)| json!({"lhs": lhs.trim(), "rhs": rhs.trim(), "assumptions": null}))
        .collect()
}

/// The name a stored evidence record goes by. A kind this table does not know keeps its own name.
fn evidence_kind(kind: &str) -> String {
    match kind {
        "research_verification" => t("独立验证"),
        "post_edit_structure" => t("修改后结构检查"),
        "structure_and_citations" => t("结构与引用检查"),
        "symbolic_math" => t("有限符号检查"),
        "reference_metadata" => t("文献元数据核验"),
        "domain_tool_evidence" => t("任务工具记录"),
        "context_snapshot" => t("冻结上下文"),
        "proposal_created" => t("提案创建"),
        "submission_rejected" => t("任务被拒绝"),
        other => return other.to_owned(),
    }
    .to_owned()
}

/// DOI and arXiv identifiers written in a message, for metadata lookup in literature and review modes.
pub fn reference_identifiers(intent: &str) -> Vec<String> {
    intent
        .split_whitespace()
        .map(|word| word.trim_matches(|c: char| matches!(c, ',' | ';' | '。' | '，' | '(' | ')' | '（' | '）')))
        .filter(|word| {
            let lower = word.to_lowercase();
            lower.starts_with("10.") && lower.contains('/')
                || lower.starts_with("arxiv:")
                || lower.starts_with("https://doi.org/")
                || lower.starts_with("https://arxiv.org/")
                || {
                    let (a, b) = lower.split_once('.').unwrap_or(("", ""));
                    a.len() == 4 && a.chars().all(|c| c.is_ascii_digit()) && (4..=5).contains(&b.len()) && b.chars().all(|c| c.is_ascii_digit())
                }
        })
        .take(3)
        .map(str::to_owned)
        .collect()
}

pub struct Workbench {
    backend: Arc<Mutex<Backend>>,
    pub project: Project,
    files: Vec<PaperFile>,
    active: Option<String>,
    active_sha: String,
    saved_text: String,
    drafts: HashMap<String, (String, String)>,
    pub editor: Entity<Editor>,
    tab: Tab,
    message: SharedString,
    proposals: Vec<Value>,
    selected_proposal: Option<String>,
    workflow: Option<Value>,
    evidence: Vec<Value>,
    engine: String,
    build: Option<Value>,
    build_notice: SharedString,
    pages: Vec<Page>,
    pdf_generation: u64,
    pdf_bytes: Option<Arc<Vec<u8>>>,
    /// Bitmap width: wanted by the current layout, last requested, and queued behind a short wait.
    pdf_target: u32,
    pdf_rendered: u32,
    pdf_queued: u32,
    zoom: f32,
    show_log: bool,
    // Assistant drawer.
    drawer: Motion,
    drawer_generation: u64,
    drawer_height: f32,
    resizing: Option<(f32, f32)>,
    mode: &'static str,
    input: Entity<Editor>,
    turns: Vec<Turn>,
    transcript: ScrollHandle,
    follow_transcript: bool,
    node_drag: Option<NodeDrag>,
    suppress_node_click: bool,
    graph_width: f32,
    // Model picker.
    picker: Motion,
    picker_generation: u64,
    providers: Vec<ProviderEntry>,
    adding_harness: bool,
    harness_name: Entity<Editor>,
    harness_command: Entity<Editor>,
    harness_models: Entity<Editor>,
    harness_error: String,
    effort_picker: Motion,
    effort_generation: u64,
    catalog: BTreeMap<String, Vec<ModelEntry>>,
    provider: String,
    model: String,
    effort: String,
    _subscriptions: Vec<Subscription>,
}

impl Workbench {
    pub fn new(backend: Arc<Mutex<Backend>>, project: Project, cx: &mut Context<Self>) -> Self {
        let editor = cx.new(|cx| Editor::new(EditorKind::Source, "", cx));
        let input = cx.new(|cx| Editor::new(EditorKind::Message, t(MODES[0].3), cx));
        let harness_name = cx.new(|cx| Editor::new(EditorKind::Message, t("名称"), cx));
        let harness_command = cx.new(|cx| Editor::new(EditorKind::Message, t("启动命令，例如 my-agent --json"), cx));
        let harness_models = cx.new(|cx| Editor::new(EditorKind::Message, t("模型列表命令（可选）"), cx));
        let subscriptions = vec![
            cx.subscribe(&harness_models, |this, _, event, cx| match event {
                EditorEvent::Submit => this.submit_harness(cx),
                EditorEvent::Dismiss => this.close_harness_form(cx),
                EditorEvent::Changed => cx.notify(),
                _ => {}
            }),
            cx.subscribe(&editor, |this, _, event, cx| match event {
                EditorEvent::Save => this.save(cx),
                EditorEvent::Changed | EditorEvent::Selection => cx.notify(),
                _ => {}
            }),
            cx.subscribe(&harness_name, |this, _, event, cx| match event {
                EditorEvent::Submit => this.submit_harness(cx),
                EditorEvent::Dismiss => this.close_harness_form(cx),
                EditorEvent::Changed => cx.notify(),
                _ => {}
            }),
            cx.subscribe(&harness_command, |this, _, event, cx| match event {
                EditorEvent::Submit => this.submit_harness(cx),
                EditorEvent::Dismiss => this.close_harness_form(cx),
                EditorEvent::Changed => cx.notify(),
                _ => {}
            }),
            cx.subscribe(&input, |this, _, event, cx| match event {
                EditorEvent::Submit => this.submit(cx),
                EditorEvent::Dismiss => this.close_picker_or_drawer(cx),
                EditorEvent::AcceptSuggestion => this.accept_suggestion(cx),
                EditorEvent::Changed => cx.notify(),
                _ => {}
            }),
        ];
        let mut this = Self {
            backend,
            project,
            files: Vec::new(),
            active: None,
            active_sha: String::new(),
            saved_text: String::new(),
            drafts: HashMap::new(),
            editor,
            tab: Tab::Source,
            message: t("正在读取项目…").into(),
            proposals: Vec::new(),
            selected_proposal: None,
            workflow: None,
            evidence: Vec::new(),
            engine: "pdflatex".into(),
            build: None,
            build_notice: SharedString::default(),
            pages: Vec::new(),
            pdf_generation: 0,
            pdf_bytes: None,
            pdf_target: 0,
            pdf_rendered: 0,
            pdf_queued: 0,
            zoom: 1.,
            show_log: false,
            drawer: Motion::Hidden,
            drawer_generation: 0,
            drawer_height: DRAWER_DEFAULT,
            resizing: None,
            mode: "chat",
            input,
            turns: Vec::new(),
            transcript: ScrollHandle::new(),
            follow_transcript: true,
            node_drag: None,
            suppress_node_click: false,
            graph_width: NODE_WIDTH,
            picker: Motion::Hidden,
            picker_generation: 0,
            providers: Vec::new(),
            adding_harness: false,
            harness_name,
            harness_command,
            harness_models,
            harness_error: String::new(),
            effort_picker: Motion::Hidden,
            effort_generation: 0,
            catalog: BTreeMap::new(),
            provider: "codex".into(),
            model: String::new(),
            effort: String::new(),
            _subscriptions: subscriptions,
        };
        this.load_project(cx);
        this.load_models(cx);
        this.load_proposals(cx);
        this.load_turns(cx);
        this.load_latest_build(cx);
        this
    }

    fn api<T: Send + 'static>(
        &self,
        cx: &mut Context<Self>,
        job: impl FnOnce(&mut Backend) -> Result<T, String> + Send + 'static,
        done: impl FnOnce(&mut Self, Result<T, String>, &mut Context<Self>) + 'static,
    ) {
        let backend = self.backend.clone();
        let task = cx.background_executor().spawn(async move {
            let mut backend = backend.lock().map_err(|_| "Backend state unavailable".to_string())?;
            job(&mut backend)
        });
        cx.spawn(async move |this, cx| {
            let result = task.await;
            this.update(cx, |this, cx| {
                done(this, result, cx);
                cx.notify();
            })
            .ok();
        })
        .detach();
    }

    pub fn dirty(&self, cx: &gpui::App) -> bool {
        !self.drafts.is_empty() || (self.active.is_some() && self.editor.read(cx).content != self.saved_text)
    }

    fn path(&self, suffix: &str) -> String {
        format!("/api/projects/{}/{}", self.project.id, suffix)
    }

    fn load_project(&mut self, cx: &mut Context<Self>) {
        let project = format!("/api/projects/{}", self.project.id);
        let files = self.path("files");
        self.api(
            cx,
            move |backend| Ok((backend.request("GET", &project, None)?, backend.request("GET", &files, None)?)),
            |this, result, cx| match result {
                Ok((project, files)) => {
                    if let Ok(project) = serde_json::from_value(project) {
                        this.project = project;
                    }
                    this.files = files
                        .as_array()
                        .map(|items| {
                            items
                                .iter()
                                .map(|item| PaperFile {
                                    path: text(item, "/path"),
                                    is_text: item["text"].as_bool().unwrap_or(false),
                                })
                                .collect()
                        })
                        .unwrap_or_default();
                    this.message = SharedString::default();
                    if this.active.is_none() {
                        let main = this.project.main_file.clone();
                        this.open_file(main, cx);
                    }
                }
                Err(error) => this.message = error.into(),
            },
        );
    }

    /// Switching files keeps unsaved text as a draft with its own hash; nothing is written or lost.
    fn open_file(&mut self, path: String, cx: &mut Context<Self>) {
        if self.active.as_deref() == Some(path.as_str()) {
            return;
        }
        if let Some(current) = self.active.clone() {
            let content = self.editor.read(cx).content.clone();
            if content != self.saved_text {
                self.drafts.insert(current, (content, self.active_sha.clone()));
            }
        }
        if let Some((content, sha)) = self.drafts.remove(&path) {
            self.active = Some(path.clone());
            self.active_sha = sha;
            self.saved_text = String::new();
            // The saved text comes back so the draft still reads as changed.
            let target = path.clone();
            let request = self.path(&format!("file?path={}", encode(&path)));
            self.editor.update(cx, |editor, cx| editor.load(content, cx));
            self.api(
                cx,
                move |backend| backend.request("GET", &request, None),
                move |this, result, _| {
                    if this.active.as_deref() == Some(target.as_str())
                        && let Ok(file) = result
                    {
                        this.saved_text = text(&file, "/content");
                    }
                },
            );
            return;
        }
        let request = self.path(&format!("file?path={}", encode(&path)));
        self.api(
            cx,
            move |backend| backend.request("GET", &request, None),
            move |this, result, cx| match result {
                Ok(file) => {
                    let content = text(&file, "/content");
                    this.active = Some(text(&file, "/path"));
                    this.active_sha = text(&file, "/sha256");
                    this.saved_text = content.clone();
                    this.editor.update(cx, |editor, cx| editor.load(content, cx));
                }
                Err(error) => this.message = error.into(),
            },
        );
    }

    fn save(&mut self, cx: &mut Context<Self>) {
        let Some(path) = self.active.clone() else { return };
        let content = self.editor.read(cx).content.clone();
        if content == self.saved_text {
            return;
        }
        let request = self.path("file");
        let body = json!({"path": path, "content": content, "expected_hash": (!self.active_sha.is_empty()).then(|| self.active_sha.clone())});
        self.message = t("正在保存…").into();
        self.api(
            cx,
            move |backend| backend.request("PUT", &request, Some(body)),
            move |this, result, cx| match result {
                Ok(saved) => {
                    if this.active.as_deref() == Some(text(&saved, "/path").as_str()) {
                        this.saved_text = content;
                        this.active_sha = text(&saved, "/sha256");
                    }
                    this.message = t("已保存").into();
                    this.load_project(cx);
                }
                Err(error) => this.message = t1("保存失败，修改仍在编辑器中：{}", error).into(),
            },
        );
    }

    fn load_models(&mut self, cx: &mut Context<Self>) {
        self.api(
            cx,
            |backend| Ok((backend.request("GET", "/api/status", None)?, backend.request("GET", "/api/runtime/models", None)?)),
            |this, result, _| {
                let Ok((status, models)) = result else { return };
                // The API route stays in the browser workbench, where it is chosen by hand.
                this.providers = models["providers"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .map(|entry| text(entry, "/provider"))
                    .filter(|id| id != "api")
                    .filter_map(|id| {
                        let report = status.pointer(&format!("/runtime/providers/{id}"))?;
                        Some(ProviderEntry { name: text(report, "/name"), kind: text(report, "/kind"), installed: report["installed"] == true, ready: report["authenticated"] == true, id })
                    })
                    // A harness Lemfolio merely knows how to run is no use to list until it is installed.
                    .filter(|entry: &ProviderEntry| entry.kind != "harness" || entry.installed)
                    .collect();
                // A selection only follows a subscription: anything billed or run another way is the author's own choice.
                if !this.providers.iter().any(|item| item.id == this.provider && item.ready) {
                    if let Some(first) = this.providers.iter().find(|item| item.kind == "subscription" && item.ready) {
                        this.provider = first.id.clone();
                    }
                }
                for entry in models["providers"].as_array().into_iter().flatten() {
                    let list = entry["models"]
                        .as_array()
                        .map(|items| {
                            items
                                .iter()
                                .map(|item| ModelEntry {
                                    id: text(item, "/id"),
                                    name: text(item, "/name"),
                                    efforts: item["efforts"].as_array().map(|levels| levels.iter().filter_map(|level| level.as_str().map(str::to_owned)).collect()).unwrap_or_default(),
                                    default_effort: item["default_effort"].as_str().map(str::to_owned),
                                })
                                .collect()
                        })
                        .unwrap_or_default();
                    this.catalog.insert(text(entry, "/provider"), list);
                }
            },
        );
    }

    fn load_proposals(&mut self, cx: &mut Context<Self>) {
        let request = self.path("proposals");
        self.api(
            cx,
            move |backend| backend.request("GET", &request, None),
            |this, result, _| match result {
                Ok(list) => {
                    this.proposals = list.as_array().cloned().unwrap_or_default();
                    let pending = this.proposals.iter().find(|item| item["status"] == "pending");
                    if this.selected_proposal.as_ref().is_none_or(|id| !this.proposals.iter().any(|item| &text(item, "/id") == id)) {
                        this.selected_proposal = pending.or(this.proposals.first()).map(|item| text(item, "/id"));
                    }
                }
                Err(error) => this.message = error.into(),
            },
        );
    }

    fn load_workflow(&mut self, cx: &mut Context<Self>) {
        let request = self.path("workflow");
        self.api(cx, move |backend| backend.request("GET", &request, None), |this, result, _| match result {
            Ok(view) => this.workflow = Some(view),
            Err(error) => this.message = error.into(),
        });
    }

    fn load_evidence(&mut self, cx: &mut Context<Self>) {
        let request = self.path("evidence");
        self.api(cx, move |backend| backend.request("GET", &request, None), |this, result, _| match result {
            Ok(list) => this.evidence = list.as_array().cloned().unwrap_or_default(),
            Err(error) => this.message = error.into(),
        });
    }

    fn select_tab(&mut self, tab: Tab, cx: &mut Context<Self>) {
        self.tab = tab;
        match tab {
            Tab::Review => self.load_proposals(cx),
            Tab::Workflow => self.load_workflow(cx),
            Tab::Evidence => self.load_evidence(cx),
            Tab::Source => {}
        }
        cx.notify();
    }

    fn decide(&mut self, proposal: Value, accept: bool, cx: &mut Context<Self>) {
        if accept && self.dirty(cx) {
            self.message = t("请先保存源码，接受提案会改写文件").into();
            cx.notify();
            return;
        }
        let request = self.path(&format!("proposals/{}/decide", text(&proposal, "/id")));
        let body = json!({"decision": if accept { "accept" } else { "reject" }, "digest": text(&proposal, "/digest"), "engine": self.engine});
        self.api(
            cx,
            move |backend| backend.request("POST", &request, Some(body)),
            move |this, result, cx| match result {
                Ok(decision) => {
                    this.message = if decision["apply_status"] == "applied" { t("修改已应用；独立验证单独记录，仍需人工审阅") } else { t("提案已拒绝，文件未改动") }.into();
                    this.load_proposals(cx);
                    this.refresh_graphs(cx);
                    if decision["apply_status"] == "applied" {
                        // The applied source replaces what the editor holds.
                        this.active = None;
                        this.saved_text.clear();
                        this.load_project(cx);
                        this.load_latest_build(cx);
                    }
                }
                Err(error) => this.message = error.into(),
            },
        );
    }

    fn load_latest_build(&mut self, cx: &mut Context<Self>) {
        let request = self.path("builds");
        self.api(cx, move |backend| backend.request("GET", &request, None), |this, result, cx| {
            let Ok(list) = result else { return };
            let latest = list.as_array().and_then(|items| items.first().cloned());
            match latest {
                Some(build) if build["state_revision"].as_str() == Some(this.project.state_revision.as_str()) => {
                    let id = text(&build, "/id");
                    this.track_build(id, cx);
                }
                Some(_) => this.build_notice = t("最近的编译属于旧版本，请重新编译").into(),
                None => this.build_notice = t("尚未编译").into(),
            }
        });
    }

    fn compile(&mut self, cx: &mut Context<Self>) {
        if self.dirty(cx) {
            self.message = t("请先保存源码，编译使用已保存版本").into();
            cx.notify();
            return;
        }
        let request = self.path("builds");
        let body = json!({"main_file": self.project.main_file, "engine": self.engine});
        self.build_notice = t("正在编译…").into();
        self.show_log = false;
        self.api(cx, move |backend| backend.request("POST", &request, Some(body)), |this, result, cx| match result {
            Ok(build) => {
                let id = text(&build, "/id");
                this.build = Some(build);
                this.track_build(id, cx);
            }
            Err(error) => this.build_notice = error.into(),
        });
    }

    fn track_build(&mut self, id: String, cx: &mut Context<Self>) {
        let request = format!("/api/builds/{id}");
        self.api(cx, move |backend| backend.request("GET", &request, None), move |this, result, cx| {
            let build = match result {
                Ok(build) => build,
                Err(error) => {
                    this.build_notice = error.into();
                    return;
                }
            };
            let status = text(&build, "/status");
            this.build = Some(build.clone());
            if !matches!(status.as_str(), "queued" | "running") {
                this.refresh_graphs(cx);
            }
            match status.as_str() {
                "queued" | "running" => {
                    this.build_notice = t("正在编译…").into();
                    cx.spawn(async move |this, cx| {
                        cx.background_executor().timer(Duration::from_millis(700)).await;
                        this.update(cx, |this, cx| this.track_build(id, cx)).ok();
                    })
                    .detach();
                }
                "success" => {
                    this.build_notice = t("编译成功").into();
                    this.load_pdf(&build, cx);
                }
                _ => {
                    this.build_notice = t1("编译未成功：{}", status).into();
                    this.show_log = true;
                }
            }
        });
    }

    fn load_pdf(&mut self, build: &Value, cx: &mut Context<Self>) {
        let Some(name) = build["artifacts"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|item| text(item, "/name"))
            .find(|name| name.to_lowercase().ends_with(".pdf"))
        else {
            self.build_notice = t("这次编译没有产出 PDF").into();
            return;
        };
        let request = format!("/api/builds/{}/artifacts/{}", text(build, "/id"), encode(&name));
        self.pdf_generation += 1;
        let generation = self.pdf_generation;
        self.api(cx, move |backend| backend.request_bytes("GET", &request, "application/json", &[]), move |this, result, cx| {
            let bytes = match result {
                Ok(bytes) => bytes,
                Err(error) => {
                    this.build_notice = error.into();
                    return;
                }
            };
            if this.pdf_generation == generation {
                this.pdf_bytes = Some(Arc::new(bytes));
                this.render_pdf(cx);
            }
        });
    }

    /// Draws the held PDF at the width the layout wants. Each new page replaces the old one in
    /// place, so resizing never empties the pane.
    fn render_pdf(&mut self, cx: &mut Context<Self>) {
        let Some(bytes) = self.pdf_bytes.clone() else { return };
        self.pdf_generation += 1;
        let generation = self.pdf_generation;
        let width = if self.pdf_target == 0 { 1200 } else { self.pdf_target };
        self.pdf_rendered = width;
        self.pdf_queued = width;
        let receiver = Arc::new(Mutex::new(pdf::render_pages(bytes, width)));
        cx.spawn(async move |this, cx| {
            let mut index = 0;
            loop {
                let source = receiver.clone();
                let page = cx
                    .background_executor()
                    .spawn(async move { source.lock().ok().and_then(|channel| channel.recv().ok()) })
                    .await;
                let Some(page) = page else { break };
                let alive = this.update(cx, |this, cx| {
                    if this.pdf_generation != generation {
                        return false;
                    }
                    match page {
                        Ok(page) => {
                            if let Some(slot) = this.pages.get_mut(index) {
                                std::mem::replace(slot, page).image.remove_asset(cx);
                            } else {
                                this.pages.push(page);
                            }
                            index += 1;
                        }
                        Err(error) => this.build_notice = t1("PDF 无法显示：{}", error).into(),
                    }
                    cx.notify();
                    true
                });
                if !matches!(alive, Ok(true)) {
                    return;
                }
            }
            // A shorter paper than the last one leaves pages behind.
            this.update(cx, |this, cx| {
                if this.pdf_generation == generation {
                    for page in this.pages.drain(index..) {
                        page.image.remove_asset(cx);
                    }
                    cx.notify();
                }
            })
            .ok();
        })
        .detach();
    }

    /// Resizing and zooming change the width in bursts; only where it settles gets rendered.
    fn queue_pdf_render(&mut self, cx: &mut Context<Self>) {
        let target = self.pdf_target;
        if self.pdf_bytes.is_none() || target == self.pdf_queued {
            return;
        }
        self.pdf_queued = target;
        cx.spawn(async move |this, cx| {
            cx.background_executor().timer(Duration::from_millis(160)).await;
            this.update(cx, |this, cx| {
                if this.pdf_target == target && this.pdf_queued == target {
                    this.render_pdf(cx);
                }
            })
            .ok();
        })
        .detach();
    }

    fn load_turns(&mut self, cx: &mut Context<Self>) {
        let list = format!("/api/runs?project_id={}", self.project.id);
        self.api(
            cx,
            move |backend| {
                let runs = backend.request("GET", &list, None)?;
                let mut loaded = Vec::new();
                // The list is newest first; the transcript reads top to bottom.
                let recent: Vec<&Value> = runs.as_array().into_iter().flatten().take(8).collect();
                for summary in recent.into_iter().rev() {
                    let id = text(summary, "/id");
                    let run = backend.request("GET", &format!("/api/runs/{id}"), None)?;
                    let events = backend.request("GET", &format!("/api/runs/{id}/event-log"), None)?;
                    loaded.push((run, events));
                }
                Ok(loaded)
            },
            |this, result, cx| {
                let Ok(loaded) = result else { return };
                this.turns = loaded
                    .into_iter()
                    .map(|(run, events)| {
                        let mut turn = Turn::from_run(&run);
                        turn.thinking = thinking_lines(events.as_array().map_or(&[], Vec::as_slice));
                        turn.suggestion = suggested_reply(events.as_array().map_or(&[], Vec::as_slice));
                        turn
                    })
                    .collect();
                this.show_hint(cx);
                this.refresh_graphs(cx);
                let running: Vec<String> = this.turns.iter().filter(|turn| turn.running()).map(|turn| turn.id.clone()).collect();
                for id in running {
                    this.poll_turn(id, cx);
                }
            },
        );
    }

    fn submit(&mut self, cx: &mut Context<Self>) {
        let intent = self.input.read(cx).content.trim().to_owned();
        if intent.is_empty() || self.turns.last().is_some_and(Turn::running) {
            return;
        }
        if self.dirty(cx) {
            self.message = t("请先保存源码，研究助手读取已保存版本").into();
            cx.notify();
            return;
        }
        let selection = self.active.as_ref().and_then(|path| {
            self.editor.read(cx).selected_lines().map(|(start, end)| json!({"path": path, "start_line": start, "end_line": end}))
        });
        let mode = self.mode;
        let body = json!({
            "intent": intent,
            "mode": mode,
            "provider": self.provider,
            "model": (!self.model.is_empty()).then(|| self.model.clone()),
            "effort": (!self.effort.is_empty()).then(|| self.effort.clone()),
            "engine": self.engine,
            "selection": selection,
            "math_checks": if mode == "math" { math_checks(&intent) } else { Vec::new() },
            "reference_identifiers": if matches!(mode, "literature" | "review") { reference_identifiers(&intent) } else { Vec::new() },
        });
        let request = self.path("runs");
        self.input.update(cx, |input, cx| input.load(String::new(), cx));
        self.follow_transcript = true;
        self.api(cx, move |backend| backend.request("POST", &request, Some(body)), |this, result, cx| match result {
            Ok(run) => {
                let turn = Turn::from_run(&run);
                let id = turn.id.clone();
                this.turns.push(turn);
                this.poll_turn(id, cx);
            }
            Err(error) => this.message = error.into(),
        });
    }

    fn poll_turn(&mut self, id: String, cx: &mut Context<Self>) {
        let run_path = format!("/api/runs/{id}");
        let event_path = format!("/api/runs/{id}/event-log");
        self.api(
            cx,
            move |backend| Ok((backend.request("GET", &run_path, None)?, backend.request("GET", &event_path, None)?)),
            move |this, result, cx| {
                let Ok((run, events)) = result else { return };
                let Some(turn) = this.turns.iter_mut().find(|turn| turn.id == id) else { return };
                let before = turn.status.clone();
                turn.apply(&run);
                // The projection re-reads evidence and artifacts, so it follows status changes
                // rather than every poll.
                let refresh = turn.shows_graph() && (turn.status != before || turn.graph.is_none());
                turn.thinking = thinking_lines(events.as_array().map_or(&[], Vec::as_slice));
                turn.suggestion = suggested_reply(events.as_array().map_or(&[], Vec::as_slice));
                let running = turn.running();
                this.show_hint(cx);
                if refresh {
                    this.refresh_graph(id.clone(), cx);
                }
                if this.follow_transcript {
                    this.transcript.scroll_to_bottom();
                }
                if running {
                    cx.spawn(async move |this, cx| {
                        cx.background_executor().timer(Duration::from_millis(500)).await;
                        this.update(cx, |this, cx| this.poll_turn(id, cx)).ok();
                    })
                    .detach();
                }
            },
        );
    }

    fn refresh_graphs(&mut self, cx: &mut Context<Self>) {
        let ids: Vec<String> = self.turns.iter().filter(|turn| turn.shows_graph()).map(|turn| turn.id.clone()).collect();
        for id in ids {
            self.refresh_graph(id, cx);
        }
    }

    fn refresh_graph(&mut self, id: String, cx: &mut Context<Self>) {
        let request = self.path(&format!("workflow?run_id={id}"));
        self.api(cx, move |backend| backend.request("GET", &request, None), move |this, result, cx| {
            let width = this.graph_width;
            let Some(turn) = this.turns.iter_mut().find(|turn| turn.id == id) else { return };
            match result {
                Ok(view) => {
                    let mut graph = Graph::from_view(&view, turn.graph.as_ref());
                    graph.fit(width);
                    turn.graph = Some(graph);
                    turn.graph_error = None;
                }
                Err(error) => turn.graph_error = Some(error),
            }
            // A run's own polling covers its model step; verification and builds run after it.
            let again = !turn.running() && turn.graph.as_ref().is_some_and(Graph::has_running);
            if again {
                cx.spawn(async move |this, cx| {
                    cx.background_executor().timer(Duration::from_millis(1500)).await;
                    this.update(cx, |this, cx| this.refresh_graph(id, cx)).ok();
                })
                .detach();
            }
        });
    }

    fn interrupt(&mut self, id: String, cx: &mut Context<Self>) {
        let request = format!("/api/runs/{id}/interrupt");
        self.api(cx, move |backend| backend.request("POST", &request, None), |this, result, _| {
            if let Err(error) = result {
                this.message = error.into();
            }
        });
    }

    fn create_proposal(&mut self, id: String, cx: &mut Context<Self>) {
        let request = format!("/api/runs/{id}/proposal");
        self.api(cx, move |backend| backend.request("POST", &request, None), move |this, result, cx| match result {
            Ok(proposal) => {
                if let Some(turn) = this.turns.iter_mut().find(|turn| turn.id == id) {
                    turn.proposed = true;
                }
                this.selected_proposal = Some(text(&proposal, "/id"));
                this.message = t("已生成待审提案，文件尚未改动").into();
                this.refresh_graphs(cx);
                // The tab above changes; the drawer and its conversation stay where they are.
                this.select_tab(Tab::Review, cx);
            }
            Err(error) => this.message = error.into(),
        });
    }

    fn verify(&mut self, id: String, cx: &mut Context<Self>) {
        let request = format!("/api/runs/{id}/verify?engine={}", self.engine);
        self.api(cx, move |backend| backend.request("POST", &request, None), |this, result, cx| match result {
            Ok(_) => {
                this.message = t("已提交独立验证，结果在证据页查看").into();
                this.refresh_graphs(cx);
                this.select_tab(Tab::Evidence, cx);
            }
            Err(error) => this.message = error.into(),
        });
    }

    fn set_mode(&mut self, mode: &'static str, cx: &mut Context<Self>) {
        self.mode = mode;
        self.show_hint(cx);
        cx.notify();
    }

    /// The latest finished run's suggestion, which is what Tab would accept.
    fn suggestion(&self) -> Option<String> {
        self.turns.last().filter(|turn| !turn.running()).and_then(|turn| turn.suggestion.clone())
    }

    /// The empty box shows the suggestion in grey when there is one, otherwise what this mode does.
    fn show_hint(&mut self, cx: &mut Context<Self>) {
        let mode = self.mode;
        let hint = self.suggestion().unwrap_or_else(|| MODES.iter().find(|entry| entry.0 == mode).map_or("", |entry| t(entry.3)).to_owned());
        self.input.update(cx, |input, cx| input.set_placeholder(&hint, cx));
    }

    fn accept_suggestion(&mut self, cx: &mut Context<Self>) {
        if let Some(text) = self.suggestion() {
            self.input.update(cx, |input, cx| input.accept(&text, cx));
        }
    }

    pub fn toggle_drawer(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        match self.drawer {
            Motion::Open => self.close_drawer(cx),
            _ => {
                self.drawer = Motion::Open;
                self.drawer_generation += 1;
                self.follow_transcript = true;
                self.transcript.scroll_to_bottom();
                let focus = self.input.read(cx).focus_handle.clone();
                window.focus(&focus, cx);
            }
        }
        cx.notify();
    }

    fn close_drawer(&mut self, cx: &mut Context<Self>) {
        self.close_picker(cx);
        self.drawer = Motion::Closing;
        self.drawer_generation += 1;
        let generation = self.drawer_generation;
        cx.spawn(async move |this, cx| {
            cx.background_executor().timer(Duration::from_millis(CLOSE_MS)).await;
            this.update(cx, |this, cx| {
                if this.drawer_generation == generation {
                    this.drawer = Motion::Hidden;
                    cx.notify();
                }
            })
            .ok();
        })
        .detach();
    }

    fn toggle_picker(&mut self, cx: &mut Context<Self>) {
        // A press outside closes the sheet first, and pressing the control is such a press:
        // ignoring it while the sheet is closing keeps that press from reopening it.
        match self.picker {
            Motion::Open => self.close_picker(cx),
            Motion::Hidden => {
                self.picker = Motion::Open;
                self.picker_generation += 1;
            }
            Motion::Closing => {}
        }
        cx.notify();
    }

    fn close_picker(&mut self, cx: &mut Context<Self>) {
        if self.picker != Motion::Open {
            return;
        }
        self.picker = Motion::Closing;
        self.picker_generation += 1;
        let generation = self.picker_generation;
        cx.spawn(async move |this, cx| {
            cx.background_executor().timer(Duration::from_millis(CLOSE_MS)).await;
            this.update(cx, |this, cx| {
                if this.picker_generation == generation {
                    this.picker = Motion::Hidden;
                    cx.notify();
                }
            })
            .ok();
        })
        .detach();
        cx.notify();
    }

    fn toggle_effort_picker(&mut self, cx: &mut Context<Self>) {
        match self.effort_picker {
            Motion::Open => self.close_effort_picker(cx),
            Motion::Hidden => {
                self.effort_picker = Motion::Open;
                self.effort_generation += 1;
            }
            Motion::Closing => {}
        }
        cx.notify();
    }

    fn close_effort_picker(&mut self, cx: &mut Context<Self>) {
        if self.effort_picker != Motion::Open {
            return;
        }
        self.effort_picker = Motion::Closing;
        self.effort_generation += 1;
        let generation = self.effort_generation;
        cx.spawn(async move |this, cx| {
            cx.background_executor().timer(Duration::from_millis(CLOSE_MS)).await;
            this.update(cx, |this, cx| {
                if this.effort_generation == generation {
                    this.effort_picker = Motion::Hidden;
                    cx.notify();
                }
            })
            .ok();
        })
        .detach();
        cx.notify();
    }

    fn close_picker_or_drawer(&mut self, cx: &mut Context<Self>) {
        if self.picker == Motion::Open {
            self.close_picker(cx);
        } else if self.effort_picker == Motion::Open {
            self.close_effort_picker(cx);
        } else if self.drawer == Motion::Open {
            self.close_drawer(cx);
        }
        cx.notify();
    }

    fn provider_name(&self, id: &str) -> String {
        self.providers.iter().find(|item| item.id == id).map_or_else(|| id.to_owned(), |item| item.name.clone())
    }

    fn is_custom_provider(&self) -> bool {
        self.providers.iter().any(|item| item.id == self.provider && item.kind == "custom")
    }

    fn open_harness_form(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.adding_harness = true;
        self.harness_error.clear();
        let focus = self.harness_name.read(cx).focus_handle.clone();
        window.focus(&focus, cx);
        cx.notify();
    }

    fn close_harness_form(&mut self, cx: &mut Context<Self>) {
        self.adding_harness = false;
        self.harness_error.clear();
        self.harness_name.update(cx, |input, cx| input.load(String::new(), cx));
        self.harness_command.update(cx, |input, cx| input.load(String::new(), cx));
        self.harness_models.update(cx, |input, cx| input.load(String::new(), cx));
        cx.notify();
    }

    fn submit_harness(&mut self, cx: &mut Context<Self>) {
        let name = self.harness_name.read(cx).content.trim().to_owned();
        let command = self.harness_command.read(cx).content.trim().to_owned();
        if name.is_empty() || command.is_empty() {
            self.harness_error = t("名称和启动命令都要填。").into();
            cx.notify();
            return;
        }
        let models_command = self.harness_models.read(cx).content.trim().to_owned();
        let body = if models_command.is_empty() { json!({"name": name, "command": command}) } else { json!({"name": name, "command": command, "models_command": models_command}) };
        self.api(cx, move |backend| backend.request("POST", "/api/runtime/harnesses", Some(body)), |this, result, cx| match result {
            Ok(entry) => {
                this.provider = text(&entry, "/id");
                this.model.clear();
                this.effort.clear();
                this.close_harness_form(cx);
                this.load_models(cx);
            }
            Err(error) => this.harness_error = error,
        });
    }

    fn remove_harness(&mut self, id: String, cx: &mut Context<Self>) {
        let request = format!("/api/runtime/harnesses/{id}");
        self.api(cx, move |backend| backend.request("DELETE", &request, None), move |this, result, cx| match result {
            Ok(_) => {
                if this.provider == id {
                    this.provider = "codex".into();
                    this.model.clear();
                    this.effort.clear();
                }
                this.load_models(cx);
            }
            Err(error) => this.harness_error = error,
        });
    }

    fn model_name(&self) -> String {
        if self.is_custom_provider() && self.catalog.get(&self.provider).is_none_or(|list| list.is_empty()) {
            return self.provider_name(&self.provider);
        }
        if self.model.is_empty() {
            return t("CLI 默认模型").to_owned();
        }
        self.catalog
            .get(&self.provider)
            .and_then(|list| list.iter().find(|item| item.id == self.model))
            .map_or_else(|| self.model.clone(), |item| item.name.clone())
    }

    /// A level the newly chosen model does not offer falls back to the CLI's default.
    fn keep_valid_effort(&mut self) {
        let offered = self.catalog.get(&self.provider).map_or_else(Vec::new, |models| efforts_for(models, &self.model).0);
        if !offered.contains(&self.effort) {
            self.effort.clear();
        }
    }

    /// The toolbar items, placed in the window's title bar so the workbench has one top edge.
    pub fn toolbar(&mut self, palette: &Palette, cx: &mut Context<Self>) -> AnyElement {
        let dirty = self.dirty(cx);
        let running = self.build.as_ref().is_some_and(|build| matches!(build["status"].as_str(), Some("queued" | "running")));
        let assistant_open = self.drawer == Motion::Open;
        let (hover, soft, ink) = (palette.line, palette.accent_soft, palette.ink);
        div()
            .flex()
            .items_center()
            .gap_2()
            .child(
                div()
                    .id("engine")
                    .role(Role::Button)
                    .aria_label(t("编译引擎"))
                    .occlude()
                    .h(px(28.))
                    .px_2()
                    .flex()
                    .items_center()
                    .rounded(px(8.))
                    .border_1()
                    .border_color(palette.line_strong)
                    .font_family(MONO)
                    .text_size(px(11.5))
                    .cursor_pointer()
                    .hover(move |style| style.bg(hover))
                    .child(self.engine.clone())
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.engine = match this.engine.as_str() {
                            "pdflatex" => "xelatex",
                            "xelatex" => "lualatex",
                            _ => "pdflatex",
                        }
                        .into();
                        cx.notify();
                    })),
            )
            .child(
                button("compile", "icons/play.svg", if running { t("编译中…") } else { t("编译") }, palette, true)
                    .occlude()
                    .h(px(28.))
                    .on_click(cx.listener(move |this, _, _, cx| {
                        if !running {
                            this.compile(cx);
                        }
                    })),
            )
            .child(
                button("assistant", "icons/sparkles.svg", t("研究助手"), palette, false)
                    .occlude()
                    .h(px(28.))
                    .when(assistant_open, move |this| this.bg(soft).text_color(ink))
                    .on_click(cx.listener(|this, _, window, cx| this.toggle_drawer(window, cx))),
            )
            .child(
                button("save", "icons/save.svg", if dirty { t("保存") } else { t("已保存") }, palette, false)
                    .occlude()
                    .h(px(28.))
                    .on_click(cx.listener(|this, _, _, cx| this.save(cx))),
            )
            .into_any_element()
    }

    fn file_tree(&self, palette: &Palette, cx: &mut Context<Self>) -> impl IntoElement {
        let mut folders_seen: Vec<String> = Vec::new();
        let mut rows: Vec<AnyElement> = Vec::new();
        let mut files = self.files.clone();
        files.sort_by_key(|file| (!file.path.contains('/'), file.path.clone()));
        let (active_bg, hover, muted, ink, line) = (palette.accent_soft, palette.line, palette.muted, palette.ink, palette.line_strong);
        for (index, file) in files.into_iter().enumerate() {
            let parts: Vec<&str> = file.path.split('/').collect();
            for depth in 0..parts.len() - 1 {
                let folder = parts[..=depth].join("/");
                if !folders_seen.contains(&folder) {
                    folders_seen.push(folder);
                    rows.push(
                        div()
                            .flex()
                            .items_center()
                            .gap(px(6.))
                            .h(px(26.))
                            .pl(px(8. + depth as f32 * 13.))
                            .text_color(muted)
                            .child(icon("icons/folder.svg", 14., muted))
                            .child(parts[depth].to_owned())
                            .into_any_element(),
                    );
                }
            }
            let depth = parts.len() - 1;
            let name = parts[depth].to_owned();
            let active = self.active.as_deref() == Some(file.path.as_str());
            let unsaved = self.drafts.contains_key(&file.path) || (active && self.editor.read(cx).content != self.saved_text);
            let is_main = file.path == self.project.main_file;
            let (path, is_text) = (file.path.clone(), file.is_text);
            rows.push(
                div()
                    .id(("file", index))
                    .role(Role::Button)
                    .aria_label(file.path.clone())
                    .flex()
                    .items_center()
                    .gap(px(6.))
                    .h(px(28.))
                    .pl(px(8. + depth as f32 * 13.))
                    .pr_2()
                    .rounded(px(7.))
                    .cursor_pointer()
                    .text_color(if is_text { ink } else { muted })
                    .when(active, move |this| this.bg(active_bg).font_weight(FontWeight::MEDIUM))
                    .when(!active, move |this| this.hover(move |style| style.bg(hover)))
                    .child(icon(if is_text { "icons/file-code-2.svg" } else { "icons/file.svg" }, 14., muted))
                    .child(div().flex_1().min_w_0().overflow_hidden().whitespace_nowrap().child(name))
                    .when(unsaved, |this| this.child(div().text_color(muted).child("•")))
                    .when(is_main, move |this| {
                        this.child(div().px(px(5.)).rounded(px(5.)).border_1().border_color(line).text_size(px(10.5)).text_color(muted).child(t("入口")))
                    })
                    .on_click(cx.listener(move |this, _, _, cx| {
                        if is_text {
                            this.tab = Tab::Source;
                            this.open_file(path.clone(), cx);
                            cx.notify();
                        } else {
                            this.message = t1("{} 不是文本文件", &path).into();
                            cx.notify();
                        }
                    }))
                    .into_any_element(),
            );
        }
        div()
            .id("file-tree")
            .flex()
            .flex_col()
            .flex_1()
            .min_h_0()
            .overflow_y_scroll()
            .p_2()
            .gap(px(1.))
            .children(rows)
    }

    fn tabs(&self, palette: &Palette, cx: &mut Context<Self>) -> impl IntoElement {
        let pending = self.proposals.iter().filter(|item| item["status"] == "pending").count();
        let items = [
            (Tab::Source, "icons/code-xml.svg", t("源码").to_owned()),
            (Tab::Review, "icons/file-diff.svg", if pending > 0 { format!("{} · {pending}", t("审阅")) } else { t("审阅").to_owned() }),
            (Tab::Workflow, "icons/workflow.svg", t("流程").to_owned()),
            (Tab::Evidence, "icons/shield-check.svg", t("证据").to_owned()),
        ];
        let (soft, hover, ink, muted) = (palette.accent_soft, palette.line, palette.ink, palette.ink_soft);
        div()
            .flex()
            .flex_none()
            .items_center()
            .gap_1()
            .h(px(44.))
            .px_2()
            .border_b_1()
            .border_color(palette.line)
            .children(items.into_iter().enumerate().map(|(index, (tab, glyph, label))| {
                let selected = self.tab == tab;
                div()
                    .id(("tab", index))
                    .role(Role::Tab)
                    .aria_label(label.clone())
                    .flex()
                    .items_center()
                    .gap(px(6.))
                    .h(px(30.))
                    .px(px(10.))
                    .rounded(px(8.))
                    .cursor_pointer()
                    .text_color(if selected { ink } else { muted })
                    .when(selected, move |this| this.bg(soft).font_weight(FontWeight::MEDIUM))
                    .when(!selected, move |this| this.hover(move |style| style.bg(hover)))
                    .child(icon(glyph, 15., if selected { ink } else { muted }))
                    .child(label)
                    .on_click(cx.listener(move |this, _, _, cx| this.select_tab(tab, cx)))
            }))
    }

    fn source_view(&self, palette: &Palette) -> impl IntoElement {
        div()
            .flex()
            .flex_col()
            .flex_1()
            .min_h_0()
            .child(
                div()
                    .flex()
                    .flex_none()
                    .items_center()
                    .h(px(30.))
                    .px_3()
                    .font_family(MONO)
                    .text_size(px(11.5))
                    .text_color(palette.muted)
                    .child(self.active.clone().unwrap_or_default()),
            )
            .child(div().flex_1().min_h_0().pt_1().child(self.editor.clone()))
    }

    fn review_view(&self, palette: &Palette, cx: &mut Context<Self>) -> impl IntoElement {
        let (soft, hover, ink, muted, good, attention) = (palette.accent_soft, palette.line, palette.ink, palette.muted, palette.good, palette.attention);
        let list = self.proposals.iter().enumerate().map(|(index, item)| {
            let id = text(item, "/id");
            let selected = self.selected_proposal.as_deref() == Some(id.as_str());
            let status = match item["status"].as_str() {
                Some("pending") => t("待审批"),
                Some("accepted") => t("已接受并应用"),
                _ => t("已拒绝"),
            };
            div()
                .id(("proposal", index))
                .flex()
                .flex_col()
                .flex_none()
                .p_2()
                .gap(px(2.))
                .rounded(px(8.))
                .cursor_pointer()
                .when(selected, move |this| this.bg(soft))
                .when(!selected, move |this| this.hover(move |style| style.bg(hover)))
                .child(div().text_color(ink).max_h(px(84.)).overflow_hidden().child(text(item, "/summary")))
                .child(div().text_size(px(11.5)).text_color(muted).child(format!("{} · {}", short(&id, 8), status)))
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.selected_proposal = Some(id.clone());
                    cx.notify();
                }))
        });
        let selected = self.proposals.iter().find(|item| Some(text(item, "/id")) == self.selected_proposal).cloned();
        let body = selected.map(|proposal| {
            let pending = proposal["status"] == "pending";
            let stale = proposal["base_state_revision"].as_str().is_some_and(|state| state != self.project.state_revision);
            let diff = text(&proposal, "/diff");
            let lines = diff.lines().enumerate().map(|(index, line)| {
                let (bg, color): (Option<Rgba>, Rgba) = match line.as_bytes().first() {
                    Some(b'+') if !line.starts_with("+++") => (Some(soft), ink),
                    Some(b'-') if !line.starts_with("---") => (Some(hover), attention),
                    Some(b'@') => (None, muted),
                    _ => (None, palette.ink_soft),
                };
                div().id(("diff", index)).px_3().whitespace_nowrap().when_some(bg, |this, bg| this.bg(bg)).text_color(color).child(if line.is_empty() { " ".to_owned() } else { line.to_owned() })
            });
            let (accept, reject) = (proposal.clone(), proposal.clone());
            div()
                .flex()
                .flex_col()
                .flex_1()
                .min_w_0()
                .child(
                    div()
                        .flex()
                        .flex_col()
                        .flex_none()
                        .gap(px(6.))
                        .py_2()
                        .px_3()
                        .border_b_1()
                        .border_color(palette.line)
                        .child(div().text_size(px(11.5)).text_color(if stale { attention } else { muted }).child(if stale && pending {
                            t("项目已变化，这份提案基于旧版本，不能接受")
                        } else {
                            t("提案只有在你接受后才写入文件")
                        }))
                        .when(pending, |this| {
                            this.child(
                                div()
                                    .flex()
                                    .flex_wrap()
                                    .gap_2()
                                    .when(!stale, |row| {
                                        row.child(button("accept", "icons/check.svg", t("接受并写入"), palette, true).h(px(28.)).on_click(cx.listener(move |this, _, _, cx| this.decide(accept.clone(), true, cx))))
                                    })
                                    .child(button("reject", "icons/x.svg", t("拒绝"), palette, false).h(px(28.)).on_click(cx.listener(move |this, _, _, cx| this.decide(reject.clone(), false, cx)))),
                            )
                        }),
                )
                .child(div().id("diff-body").flex_1().min_h_0().overflow_scroll().py_2().font_family(MONO).text_size(px(12.)).line_height(px(19.)).children(lines))
        });
        div()
            .flex()
            .flex_1()
            .min_h_0()
            .child(
                div()
                    .id("proposal-list")
                    .flex()
                    .flex_col()
                    .flex_none()
                    .w(px(210.))
                    .p_2()
                    .gap_1()
                    .overflow_y_scroll()
                    .border_r_1()
                    .border_color(palette.line)
                    .when(self.proposals.is_empty(), |this| this.child(div().p_2().text_color(muted).child(t("还没有提案。在研究助手的语义修改模式里提出修改。"))))
                    .children(list),
            )
            .when_some(body, |this, body| this.child(body))
            .child(div().text_color(good).w(px(0.)))
    }

    fn workflow_view(&self, palette: &Palette) -> impl IntoElement {
        let nodes = self.workflow.as_ref().and_then(|view| view["nodes"].as_array().cloned()).unwrap_or_default();
        div()
            .id("workflow")
            .flex()
            .flex_col()
            .flex_1()
            .min_h_0()
            .overflow_y_scroll()
            .p_3()
            .gap_1()
            .when(self.workflow.is_none(), |this| this.child(div().text_color(palette.muted).child(t("正在读取科研流程…"))))
            .children(nodes.iter().enumerate().map(|(index, node)| {
                let status = text(node, "/status");
                let historical = node["historical"].as_bool().unwrap_or(false);
                let color = match status.as_str() {
                    "passed" => palette.good,
                    "failed" | "blocked" | "interrupted" | "pending_review" => palette.attention,
                    _ => palette.muted,
                };
                div()
                    .id(("node", index))
                    .flex()
                    .items_center()
                    .gap_2()
                    .p_2()
                    .rounded(px(8.))
                    .border_1()
                    .border_color(palette.line)
                    .child(div().flex_1().min_w_0().text_color(palette.ink).child(text(node, "/label")))
                    .when(historical, |this| this.child(div().text_size(px(11.5)).text_color(palette.muted).child(t("历史"))))
                    .child(div().text_size(px(11.5)).font_weight(FontWeight::MEDIUM).text_color(color).child(status))
            }))
    }

    fn evidence_view(&self, palette: &Palette) -> impl IntoElement {
        div()
            .id("evidence")
            .flex()
            .flex_col()
            .flex_1()
            .min_h_0()
            .overflow_y_scroll()
            .p_3()
            .gap_1()
            .child(div().pb_2().text_size(px(12.)).text_color(palette.muted).child(t("证据按种类分开记录。模型完成不等于验收，人工结论由作者决定。")))
            .when(self.evidence.is_empty(), |this| this.child(div().text_color(palette.muted).child(t("此项目还没有证据记录。"))))
            .children(self.evidence.iter().enumerate().take(200).map(|(index, record)| {
                let current = record["state_revision"].as_str() == Some(self.project.state_revision.as_str());
                let status = record["result"]["status"].as_str().map(str::to_owned).unwrap_or_default();
                div()
                    .id(("record", index))
                    .flex()
                    .items_center()
                    .gap_2()
                    .p_2()
                    .rounded(px(8.))
                    .border_1()
                    .border_color(palette.line)
                    .child(div().flex_1().min_w_0().text_color(palette.ink).child(evidence_kind(&text(record, "/kind"))))
                    .child(div().font_family(MONO).text_size(px(11.5)).text_color(palette.muted).child(short(&text(record, "/base_revision"), 8)))
                    .when(!current, |this| this.child(div().text_size(px(11.5)).text_color(palette.muted).child(t("非当前版本"))))
                    .when(!status.is_empty(), |this| this.child(div().text_size(px(11.5)).text_color(palette.ink_soft).child(status)))
            }))
    }

    fn pdf_view(&self, scale: f32, width: f32, palette: &Palette, cx: &mut Context<Self>) -> impl IntoElement {
        let log = self.build.as_ref().map(|build| {
            let error = text(build, "/error");
            let log = text(build, "/log");
            if log.is_empty() { error } else { log }
        });
        let target = self.pdf_target;
        let hover = palette.line;
        let tool = move |id: &'static str, glyph: &'static str, label: &'static str, ink: Rgba| {
            div()
                .id(id)
                .role(Role::Button)
                .aria_label(t(label))
                .flex()
                .items_center()
                .justify_center()
                .size(px(26.))
                .rounded(px(7.))
                .cursor_pointer()
                .hover(move |style| style.bg(hover))
                .child(icon(glyph, 15., ink))
        };
        div()
            .flex()
            .flex_col()
            .flex_none()
            .w(px(width))
            .rounded(px(crate::SHEET))
            .border_1()
            .border_color(palette.line)
            .bg(palette.paper)
            .overflow_hidden()
            .child(
                div()
                    .flex()
                    .flex_none()
                    .items_center()
                    .gap_1()
                    .h(px(44.))
                    .px_2()
                    .border_b_1()
                    .border_color(palette.line)
                    .child(div().flex_1().min_w_0().px_1().text_size(px(12.)).text_color(palette.ink_soft).child(self.build_notice.clone()))
                    .child(tool("log", "icons/scroll-text.svg", "编译日志", palette.ink_soft).on_click(cx.listener(|this, _, _, cx| {
                        this.show_log = !this.show_log;
                        cx.notify();
                    })))
                    .child(tool("zoom-out", "icons/zoom-out.svg", "缩小", palette.ink_soft).on_click(cx.listener(|this, _, _, cx| {
                        this.zoom = (this.zoom / 1.2).max(0.4);
                        cx.notify();
                    })))
                    .child(tool("zoom-in", "icons/zoom-in.svg", "放大", palette.ink_soft).on_click(cx.listener(|this, _, _, cx| {
                        this.zoom = (this.zoom * 1.2).min(4.);
                        cx.notify();
                    }))),
            )
            .child(if self.show_log {
                div()
                    .id("build-log")
                    .flex_1()
                    .min_h_0()
                    .overflow_y_scroll()
                    .p_3()
                    .font_family(MONO)
                    .text_size(px(11.5))
                    .line_height(px(17.))
                    .text_color(palette.ink_soft)
                    .child(log.filter(|log| !log.is_empty()).unwrap_or_else(|| t("这次编译没有日志。").to_owned()))
                    .into_any_element()
            } else {
                div()
                    .id("pdf-pages")
                    .flex()
                    .flex_col()
                    .items_center()
                    .flex_1()
                    .min_h_0()
                    .overflow_scroll()
                    .gap_3()
                    .p(px(14.))
                    .when(self.pages.is_empty(), |this| this.child(div().pt_8().text_color(palette.muted).child(t("编译成功后，PDF 显示在这里。"))))
                    .children(self.pages.iter().map(|page| {
                        let (page_width, page_height) = pdf::display_size(page.pixels, target, scale);
                        img(page.image.clone())
                            .flex_none()
                            .w(px(page_width))
                            .h(px(page_height))
                            .object_fit(ObjectFit::Fill)
                    }))
                    .into_any_element()
            })
    }

    fn turn_view(&self, index: usize, turn: &Turn, palette: &Palette, cx: &mut Context<Self>) -> AnyElement {
        let (muted, ink, soft, line, attention, hover) = (palette.muted, palette.ink, palette.accent_soft, palette.line, palette.attention, palette.line);
        let id = turn.id.clone();
        let mode = MODES.iter().find(|entry| entry.0 == turn.mode).map_or(turn.mode.clone(), |entry| t(entry.2).to_owned());
        let provider_name = self.provider_name(&turn.provider);
        let mut backend_label = if turn.model.is_empty() { provider_name } else { format!("{provider_name} · {}", turn.model) };
        if !turn.effort.is_empty() {
            backend_label = format!("{backend_label} · {}", turn.effort);
        }
        let steps = turn.thinking.len();
        // The summary is a few lines and is what this view is for, so it starts open.
        let open = turn.running() || turn.thinking_open;
        let header = if turn.running() && steps == 0 {
            t("正在思考…").to_owned()
        } else if turn.running() {
            t1("正在思考 · {} 步", steps)
        } else if steps == 0 {
            t("这次运行没有返回摘要").to_owned()
        } else {
            t1("摘要 · {} 条", steps)
        };
        let toggle = id.clone();
        let thinking = div()
            .flex()
            .flex_col()
            .gap_1()
            .child(
                div()
                    .id(("thinking", index))
                    .flex()
                    .items_center()
                    .gap(px(5.))
                    .text_size(px(12.))
                    .text_color(muted)
                    .when(steps > 0, |this| this.cursor_pointer())
                    .hover(move |style| style.text_color(ink))
                    .child(svg().path(if open { "icons/chevron-down.svg" } else { "icons/chevron-right.svg" }).flex_none().size(px(13.)).text_color(muted))
                    .child(header)
                    .on_click(cx.listener(move |this, _, _, cx| {
                        if let Some(turn) = this.turns.iter_mut().find(|turn| turn.id == toggle) {
                            turn.thinking_open = !turn.thinking_open;
                            cx.notify();
                        }
                    })),
            )
            .when(open && steps > 0, |this| {
                this.child(
                    div()
                        .flex()
                        .flex_col()
                        .gap(px(3.))
                        .ml(px(6.))
                        .pl(px(11.))
                        .border_l_1()
                        .border_color(line)
                        .text_size(px(12.))
                        .line_height(px(18.))
                        .text_color(muted)
                        .children(turn.thinking.iter().map(|step| div().child(step.clone()))),
                )
            });
        let proposal_id = id.clone();
        let verify_id = id.clone();
        let can_propose = !turn.changes.is_empty() && turn.status == "completed" && !turn.proposed;
        div()
            .flex()
            .flex_col()
            .gap_2()
            .child(
                div().flex().justify_end().child(
                    div().max_w(relative_width()).px_3().py(px(7.)).rounded(px(11.)).bg(soft).text_color(ink).child(turn.intent.clone()),
                ),
            )
            .child(div().text_size(px(11.)).text_color(muted).child(format!("{mode} · {backend_label}")))
            .when(turn.shows_graph(), |this| this.child(self.graph_view(index, turn, palette, cx)))
            .when(!turn.shows_graph(), |this| this.child(thinking))
            .when_some(turn.error.clone(), |this, error| this.child(div().text_color(attention).child(error)))
            .when_some(turn.answer.clone(), |this, answer| {
                this.child(div().flex().flex_col().gap_1().child(div().text_color(ink).line_height(px(21.)).child(answer)).child(div().text_size(px(11.)).text_color(muted).child(t("模型输出，未经验证"))))
            })
            .when(!turn.checks.is_empty(), |this| {
                this.child(
                    div()
                        .flex()
                        .flex_col()
                        .gap(px(2.))
                        .text_size(px(12.))
                        .text_color(muted)
                        .child(t("模型自述的检查（未经独立验证）"))
                        .children(turn.checks.iter().map(|check| div().child(format!("· {check}")))),
                )
            })
            .when(!turn.changes.is_empty() && turn.shows_graph(), |this| {
                this.child(div().font_family(MONO).text_size(px(11.5)).text_color(muted).child(turn.changes.join("  ")))
            })
            .when(!turn.diffs.is_empty() && !turn.shows_graph(), |this| this.child(run_view::diff_view(&turn.diffs, palette)))
            .when(turn.status == "completed", |this| {
                this.child(
                    div()
                        .flex()
                        .flex_wrap()
                        .gap_2()
                        .when(can_propose, |row| {
                            row.child(button(leak_id("propose", &proposal_id), "icons/file-diff.svg", t("生成提案并打开审阅"), palette, false).h(px(26.)).text_size(px(12.)).on_click(cx.listener(move |this, _, _, cx| this.create_proposal(proposal_id.clone(), cx))))
                        })
                        .child(
                            div()
                                .id(("verify", index))
                                .role(Role::Button)
                                .flex()
                                .items_center()
                                .gap(px(5.))
                                .h(px(26.))
                                .px_2()
                                .rounded(px(7.))
                                .cursor_pointer()
                                .text_size(px(12.))
                                .text_color(muted)
                                .hover(move |style| style.bg(hover))
                                .child(icon("icons/shield-check.svg", 13., muted))
                                .child(t("独立验证此任务"))
                                .on_click(cx.listener(move |this, _, _, cx| this.verify(verify_id.clone(), cx))),
                        ),
                )
            })
            .into_any_element()
    }

    /// A Claude run's workflow. Dragging moves the card; a press that does not move opens its record.
    fn graph_view(&self, turn_index: usize, turn: &Turn, palette: &Palette, cx: &mut Context<Self>) -> AnyElement {
        let muted = palette.muted;
        let Some(graph) = &turn.graph else {
            let notice = turn.graph_error.clone().map_or_else(|| t("正在读取工作流…").to_owned(), |error| t1("工作流读取失败：{}", error));
            return div().text_size(px(12.)).text_color(if turn.graph_error.is_some() { palette.attention } else { muted }).child(notice).into_any_element();
        };
        let dragged = self.node_drag.as_ref().filter(|drag| drag.run == turn.id && drag.moved).map(|drag| drag.node);
        // Painted last, the card in hand stays above the others.
        let mut order: Vec<usize> = (0..graph.nodes.len()).filter(|index| Some(*index) != dragged).collect();
        order.extend(dragged);
        let (surface, line, strong, ink, accent) = (palette.surface, palette.line, palette.line_strong, palette.ink, palette.accent);
        let cards: Vec<_> = order.into_iter().map(|index| {
            let node = &graph.nodes[index];
            let (x, y) = graph.positions[index];
            let selected = graph.selected.as_deref() == Some(node.id.as_str());
            let held = dragged == Some(index);
            let color = run_view::status_color(&node.status, palette);
            let label = run_view::node_label(node);
            let status = run_view::status_label(&node.status).to_owned();
            let dot = div().flex_none().size(px(7.)).rounded(px(4.)).bg(color);
            let dot: AnyElement = if node.status == "running" {
                dot.with_animation(("graph-pulse", turn_index * 16 + index), Animation::new(Duration::from_millis(1100)).repeat().with_easing(pulsating_between(0.3, 1.)), |element, value| element.opacity(value)).into_any_element()
            } else {
                dot.into_any_element()
            };
            let (press_run, click_run, node_id) = (turn.id.clone(), turn.id.clone(), node.id.clone());
            div()
                .id(("graph-node", turn_index * 16 + index))
                .role(Role::Button)
                .aria_label(format!("{label} · {status}"))
                .absolute()
                .left(px(x))
                .top(px(y))
                .w(px(NODE_WIDTH))
                .h(px(NODE_HEIGHT))
                .flex()
                .flex_col()
                .justify_center()
                .gap(px(2.))
                .px(px(7.))
                .rounded(px(10.))
                .border_1()
                .border_color(if selected { accent } else { strong })
                .bg(surface)
                .when(held, |this| this.shadow_lg().cursor_grabbing())
                .when(!held, move |this| this.cursor_grab().hover(move |style| style.border_color(ink)))
                .child(div().overflow_hidden().whitespace_nowrap().text_size(px(12.)).font_weight(FontWeight::MEDIUM).text_color(ink).child(label))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap(px(4.))
                        .overflow_hidden()
                        .whitespace_nowrap()
                        .text_size(px(10.5))
                        .text_color(muted)
                        .child(dot)
                        .child(status)
                        .when(node.historical, |this| this.child(format!("· {}", t("历史")))),
                )
                .on_mouse_down(MouseButton::Left, cx.listener(move |this, event: &MouseDownEvent, _, cx| {
                    this.suppress_node_click = false;
                    this.follow_transcript = false;
                    this.node_drag = Some(NodeDrag { run: press_run.clone(), node: index, start: (f32::from(event.position.x), f32::from(event.position.y)), origin: (x, y), moved: false });
                    cx.notify();
                }))
                .on_click(cx.listener(move |this, _, _, cx| {
                    // The end of a drag also arrives as a click on the card.
                    let moved = this.node_drag.as_ref().is_some_and(|drag| drag.moved);
                    if std::mem::take(&mut this.suppress_node_click) || moved {
                        return;
                    }
                    if let Some(graph) = this.turns.iter_mut().find(|turn| turn.id == click_run).and_then(|turn| turn.graph.as_mut()) {
                        graph.selected = if graph.selected.as_deref() == Some(node_id.as_str()) { None } else { Some(node_id.clone()) };
                        cx.notify();
                    }
                }))
        }).collect();
        let record = graph.selected.as_ref().and_then(|id| graph.nodes.iter().find(|node| &node.id == id)).map(|node| {
            div()
                .flex()
                .flex_col()
                .gap(px(2.))
                .p_2()
                .rounded(px(8.))
                .border_1()
                .border_color(line)
                .text_size(px(11.5))
                .child(div().pb(px(2.)).text_color(ink).font_weight(FontWeight::MEDIUM).child(format!("{} · {}", run_view::node_label(node), t1("{} 条证据", node.evidence))))
                .when(node.details.is_empty(), |this| this.child(div().text_color(muted).child(t("此步骤没有记录"))))
                .children(node.details.iter().map(|(key, value)| {
                    div()
                        .flex()
                        .gap_2()
                        .font_family(MONO)
                        .text_size(px(11.))
                        .line_height(px(16.))
                        .child(div().flex_none().w(px(190.)).overflow_hidden().whitespace_nowrap().text_color(muted).child(key.clone()))
                        .child(div().flex_1().min_w_0().text_color(palette.ink_soft).child(value.clone()))
                }))
        });
        let reset_run = turn.id.clone();
        div()
            .flex()
            .flex_col()
            .gap(px(6.))
            .child(
                div()
                    .relative()
                    .w(px(self.graph_width))
                    .h(px(graph.height()))
                    .child(run_view::edges_element(graph, palette))
                    .children(cards),
            )
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap_2()
                    .text_size(px(11.))
                    .text_color(muted)
                    .child(t("拖动节点调整布局，点击查看记录"))
                    .when(graph.arranged, |this| {
                        this.child(
                            div()
                                .id(("graph-reset", turn_index))
                                .role(Role::Button)
                                .cursor_pointer()
                                .text_color(palette.ink_soft)
                                .hover(move |style| style.text_color(ink))
                                .child(t("恢复默认布局"))
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    if let Some(graph) = this.turns.iter_mut().find(|turn| turn.id == reset_run).and_then(|turn| turn.graph.as_mut()) {
                                        graph.reset();
                                        cx.notify();
                                    }
                                })),
                        )
                    }),
            )
            .when_some(record, |this, record| this.child(record))
            .into_any_element()
    }

    /// Reference motion: the list grows out of the control as a soft blob, its text comes in as it
    /// settles, and it shrinks back into the same control.
    fn picker_view(&self, palette: &Palette, cx: &mut Context<Self>) -> impl IntoElement {
        let empty = Vec::new();
        let models = self.catalog.get(&self.provider).unwrap_or(&empty);
        let custom = self.is_custom_provider();
        let adding = self.adding_harness;
        let listed = !adding && !(custom && models.is_empty());
        let row_count = if listed { models.len() + 1 } else { 0 } + self.providers.len() + 1;
        // One text line plus the padding and border, so a line fits and the editor has nothing to scroll.
        let field_height = 20. + 12. + 2.;
        let command_height = (self.harness_command.read(cx).content_height.max(20.) + 14.).min(94.);
        let models_height = (self.harness_models.read(cx).content_height.max(20.) + 14.).min(94.);
        let form_height = if adding { 210. + command_height + models_height } else { 0. };
        let target_height = (row_count as f32 * 34. + 72. + form_height).min(if adding { 560. } else { 460. });
        let target_width: f32 = 300.;
        let opening = self.picker == Motion::Open;
        let duration = Duration::from_millis(if opening { OPEN_MS } else { CLOSE_MS });
        let (hover, soft, ink, muted, good) = (palette.glass_hover, palette.glass_selected, palette.ink, palette.muted, palette.good);
        let provider_rows = self.providers.iter().enumerate().map(|(index, entry)| {
            let selected = entry.id == self.provider;
            let (id, ready, name, removable) = (entry.id.clone(), entry.ready, &entry.name, entry.kind == "custom");
            let remove_id = id.clone();
            let state = match (entry.kind.as_str(), ready) {
                ("subscription", true) => t("已登录"),
                ("subscription", false) if entry.installed => t("未登录"),
                ("subscription", false) => t("未安装"),
                (_, true) => t("可用"),
                (_, false) => t("未找到"),
            };
            div()
                .id(("provider", index))
                .role(Role::Button)
                .aria_label(name.clone())
                .flex()
                .flex_none()
                .items_center()
                .gap_2()
                .h(px(32.))
                .px_2()
                .rounded(px(8.))
                .when(ready, |this| this.cursor_pointer())
                .text_color(if ready { ink } else { muted })
                .when(selected, move |this| this.bg(soft))
                .when(!selected, move |this| this.hover(move |style| style.bg(hover)))
                .child(icon("icons/terminal.svg", 14., muted))
                .child(div().flex_1().min_w_0().overflow_hidden().whitespace_nowrap().child(name.clone()))
                .child(div().text_size(px(11.)).text_color(if ready { good } else { muted }).child(state))
                .when(removable, |this| {
                    this.child(
                        div()
                            .id(("remove-harness", index))
                            .role(Role::Button)
                            .aria_label(t1("移除 {}", name))
                            .cursor_pointer()
                            .rounded(px(4.))
                            .hover(move |style| style.bg(hover))
                            .child(icon("icons/x.svg", 12., muted))
                            .on_click(cx.listener(move |this, _, _, cx| {
                                cx.stop_propagation();
                                this.remove_harness(remove_id.clone(), cx);
                            })),
                    )
                })
                .on_click(cx.listener(move |this, _, _, cx| {
                    if ready {
                        this.provider = id.clone();
                        this.model.clear();
                        this.effort.clear();
                        cx.notify();
                    }
                }))
        });
        let add_row = div()
            .id("add-harness")
            .role(Role::Button)
            .aria_label(t("添加自选 Harness"))
            .flex()
            .flex_none()
            .items_center()
            .gap_2()
            .h(px(32.))
            .px_2()
            .rounded(px(8.))
            .cursor_pointer()
            .text_color(ink)
            .hover(move |style| style.bg(hover))
            .child(icon("icons/plus.svg", 14., muted))
            .child(div().flex_1().child(t("添加自选 Harness")))
            .on_click(cx.listener(|this, _, window, cx| {
                if this.adding_harness {
                    this.close_harness_form(cx);
                } else {
                    this.open_harness_form(window, cx);
                }
            }));
        let field = |editor: &Entity<Editor>, height: f32| {
            let focus = editor.read(cx).focus_handle.clone();
            div()
                .flex_none()
                .cursor_text()
                .on_mouse_down(MouseButton::Left, move |_, window, cx| window.focus(&focus, cx))
                .h(px(height))
                .px_2()
                .py(px(6.))
                .rounded(px(8.))
                .border_1()
                .border_color(palette.glass_rim)
                .bg(palette.glass_hover)
                .overflow_hidden()
                .child(editor.clone())
        };
        let harness_form = div()
            .flex()
            .flex_col()
            .flex_none()
            .gap_1()
            .px_2()
            .pb_1()
            .child(field(&self.harness_name, field_height))
            .child(field(&self.harness_command, command_height))
            .child(field(&self.harness_models, models_height))
            .child(div().text_size(px(11.)).text_color(muted).child(t("启动命令里写 {model} 和 {effort}，选中的值会填入。模型列表命令每行输出一个模型；要报告 Effort，就输出 JSON。")))
            .child(div().text_size(px(11.)).text_color(muted).child(t("任何命令行 agent 都行。Lemfolio 把提示词写到它的标准输入，从标准输出读结果 JSON。")))
            .child(div().text_size(px(11.)).text_color(muted).child(t("它能用哪些工具，Lemfolio 限制不了。写入项目仍要你审阅接受。")))
            .when(!self.harness_error.is_empty(), |this| this.child(div().text_size(px(11.)).text_color(palette.attention).child(self.harness_error.clone())))
            .child(
                div()
                    .id("add-harness-confirm")
                    .role(Role::Button)
                    .flex()
                    .flex_none()
                    .justify_center()
                    .items_center()
                    .h(px(28.))
                    .rounded(px(8.))
                    .cursor_pointer()
                    .bg(soft)
                    .text_color(ink)
                    .child(t("添加"))
                    .on_click(cx.listener(|this, _, _, cx| this.submit_harness(cx))),
            );
        let default_row = {
            let selected = self.model.is_empty();
            div()
                .id("model-default")
                .role(Role::Button)
                .aria_label(t("CLI 默认模型"))
                .flex()
                .flex_none()
                .items_center()
                .gap_2()
                .h(px(32.))
                .px_2()
                .rounded(px(8.))
                .cursor_pointer()
                .text_color(ink)
                .hover(move |style| style.bg(hover))
                .child(div().flex_1().child(t("CLI 默认模型")))
                .when(selected, |this| this.child(icon("icons/check.svg", 14., ink)))
                .on_click(cx.listener(|this, _, _, cx| {
                    this.model.clear();
                    this.keep_valid_effort();
                    this.close_picker(cx);
                }))
        };
        let model_rows = models.iter().enumerate().map(|(index, entry)| {
            let selected = entry.id == self.model;
            let id = entry.id.clone();
            let name = &entry.name;
            div()
                .id(("model", index))
                .role(Role::Button)
                .aria_label(name.clone())
                .flex()
                .flex_none()
                .items_center()
                .gap_2()
                .h(px(32.))
                .px_2()
                .rounded(px(8.))
                .cursor_pointer()
                .text_color(ink)
                .hover(move |style| style.bg(hover))
                .child(div().flex_1().min_w_0().overflow_hidden().whitespace_nowrap().child(name.clone()))
                .when(selected, |this| this.child(icon("icons/check.svg", 14., ink)))
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.model = id.clone();
                    this.keep_valid_effort();
                    this.close_picker(cx);
                }))
        });
        let content = div()
            .id("picker-scroll")
            .flex()
            .flex_col()
            .flex_none()
            .w(px(target_width - 2.))
            .h(px(target_height - 2.))
            .p_2()
            .gap(px(2.))
            .overflow_y_scroll()
            .child(div().px_2().pb_1().text_size(px(11.)).text_color(muted).child(t("模型后端")))
            .children(provider_rows)
            .child(add_row)
            .when(self.adding_harness, |this| this.child(harness_form))
            .when(listed, |this| {
                this.child(div().px_2().pt_2().pb_1().text_size(px(11.)).text_color(muted).child(t("模型")))
                    .child(default_row)
                    .children(model_rows)
            })
            .when(custom && !adding && models.is_empty(), |this| this.child(div().px_2().pt_2().text_size(px(11.)).text_color(muted).child(t("模型由启动命令决定。"))));
        let id: SharedString = format!("picker-{}", self.picker_generation).into();
        let (sheen, glow) = (glass_sheen(palette), glass_glow(palette));
        deferred(
            div()
                .absolute()
                .bottom(px(50.))
                .right(px(14.))
                .w(px(target_width))
                .h(px(target_height))
                .flex()
                .flex_col()
                .items_end()
                .justify_end()
                .child(
                    glass_surface(div().id("picker-sheet"), palette)
                        .occlude()
                        .relative()
                        .flex_none()
                        .overflow_hidden()
                        .on_mouse_down_out(cx.listener(|this, _, _, cx| this.close_picker(cx)))
                        .child(sheen)
                        .child(glow)
                        .child(content)
                        .with_animation(
                            id,
                            Animation::new(duration).with_easing(if opening { spring } else { ease_in }),
                            move |element, value| {
                                let progress = if opening { value } else { 1. - value };
                                let clamped = progress.clamp(0., 1.);
                                // Starts as the control's own pill, wider than tall, and rounds off.
                                let width = 150. + (target_width - 150.) * progress;
                                let height = 34. + (target_height - 34.) * progress;
                                element
                                    .w(px(width.max(0.)))
                                    .h(px(height.max(0.)))
                                    .rounded(px(17. - 3. * clamped))
                                    .opacity((clamped * 1.6).min(1.))
                            },
                        ),
                ),
        )
        .with_priority(2)
    }

    /// Effort has its own popup: the same soft growth out of its control, on the same glass.
    fn effort_view(&self, palette: &Palette, cx: &mut Context<Self>) -> impl IntoElement {
        let empty = Vec::new();
        let (efforts, default_effort) = efforts_for(self.catalog.get(&self.provider).unwrap_or(&empty), &self.model);
        // The CLI's own default comes first, then the levels the provider reports for this model.
        let options: Vec<(String, String)> = std::iter::once((String::new(), default_effort.map_or_else(|| t("默认").to_owned(), |level| t1("默认 · {}", &level)))).chain(efforts.into_iter().map(|level| (level.clone(), level))).collect();
        let target_height = options.len() as f32 * 34. + 56.;
        let target_width: f32 = 170.;
        let opening = self.effort_picker == Motion::Open;
        let duration = Duration::from_millis(if opening { OPEN_MS } else { CLOSE_MS });
        let (hover, soft, ink, muted) = (palette.glass_hover, palette.glass_selected, palette.ink, palette.muted);
        let rows = options.into_iter().enumerate().map(|(index, (value, label))| {
            let selected = value == self.effort;
            div()
                .id(("effort", index))
                .role(Role::Button)
                .aria_label(format!("Effort {label}"))
                .flex()
                .flex_none()
                .items_center()
                .gap_2()
                .h(px(32.))
                .px_2()
                .rounded(px(8.))
                .cursor_pointer()
                .text_color(ink)
                .when(selected, move |this| this.bg(soft))
                .when(!selected, move |this| this.hover(move |style| style.bg(hover)))
                .child(div().flex_1().min_w_0().overflow_hidden().whitespace_nowrap().child(label))
                .when(selected, |this| this.child(icon("icons/check.svg", 14., ink)))
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.effort = value.clone();
                    this.close_effort_picker(cx);
                }))
        });
        let content = div()
            .flex()
            .flex_col()
            .flex_none()
            .w(px(target_width - 2.))
            .h(px(target_height - 2.))
            .p_2()
            .gap(px(2.))
            .child(div().px_2().pb_1().text_size(px(11.)).text_color(muted).child("Effort"))
            .children(rows);
        let id: SharedString = format!("effort-{}", self.effort_generation).into();
        deferred(
            div()
                .absolute()
                .bottom(px(50.))
                .right(px(52.))
                .w(px(target_width))
                .h(px(target_height))
                .flex()
                .flex_col()
                .items_end()
                .justify_end()
                .child(
                    glass_surface(div().id("effort-sheet"), palette)
                        .occlude()
                        .relative()
                        .flex_none()
                        .overflow_hidden()
                        .on_mouse_down_out(cx.listener(|this, _, _, cx| this.close_effort_picker(cx)))
                        .child(glass_sheen(palette))
                        .child(glass_glow(palette))
                        .child(content)
                        .with_animation(
                            id,
                            Animation::new(duration).with_easing(if opening { spring } else { ease_in }),
                            move |element, value| {
                                let progress = if opening { value } else { 1. - value };
                                let clamped = progress.clamp(0., 1.);
                                let width = 90. + (target_width - 90.) * progress;
                                let height = 34. + (target_height - 34.) * progress;
                                element
                                    .w(px(width.max(0.)))
                                    .h(px(height.max(0.)))
                                    .rounded(px(17. - 3. * clamped))
                                    .opacity((clamped * 1.6).min(1.))
                            },
                        ),
                ),
        )
        .with_priority(2)
    }

    fn drawer_view(&self, palette: &Palette, cx: &mut Context<Self>) -> impl IntoElement {
        let (soft, hover, ink, muted) = (palette.accent_soft, palette.line, palette.ink, palette.ink_soft);
        let rail: Vec<_> = MODES.iter().enumerate().map(|(index, (id, glyph, name, hint))| {
            let selected = self.mode == *id;
            let id = *id;
            div()
                .id(("mode", index))
                .role(Role::RadioButton)
                .aria_label(t(name))
                .aria_description(t(hint))
                .flex()
                .items_center()
                .gap(px(7.))
                .h(px(32.))
                .px(px(9.))
                .rounded(px(8.))
                .cursor_pointer()
                .text_size(px(12.5))
                .text_color(if selected { ink } else { muted })
                .when(selected, move |this| this.bg(soft).font_weight(FontWeight::MEDIUM))
                .when(!selected, move |this| this.hover(move |style| style.bg(hover)))
                .child(icon(glyph, 15., if selected { ink } else { muted }))
                .child(t(name))
                .on_click(cx.listener(move |this, _, _, cx| this.set_mode(id, cx)))
        }).collect();
        let running = self.turns.last().filter(|turn| turn.running()).map(|turn| turn.id.clone());
        let input_height = (self.input.read(cx).content_height.max(20.) + 2.).min(102.);
        let selection = self.active.as_ref().and_then(|path| self.editor.read(cx).selected_lines().map(|(a, b)| format!("{path}:{a}–{b}")));
        let can_send = !self.input.read(cx).content.trim().is_empty();
        let accent = palette.accent;
        let accent_ink = palette.accent_ink;
        let line = palette.line;
        let line_strong = palette.line_strong;
        let picker_open = self.picker != Motion::Hidden;
        let effort_open = self.effort_picker != Motion::Hidden;
        let offers_effort = !efforts_for(self.catalog.get(&self.provider).map_or(&[][..], |list| list.as_slice()), &self.model).0.is_empty();
        let height = self.drawer_height;
        let opening = self.drawer == Motion::Open;
        let duration = Duration::from_millis(if opening { OPEN_MS } else { CLOSE_MS });
        let turns: Vec<AnyElement> = self.turns.iter().enumerate().map(|(index, turn)| self.turn_view(index, turn, palette, cx)).collect();
        let id: SharedString = format!("drawer-{}", self.drawer_generation).into();
        div()
            .relative()
            .flex_none()
            .overflow_hidden()
            .mx(px(6.))
            .mb(px(6.))
            .rounded(px(14.))
            .border_1()
            .border_color(palette.line_strong)
            .bg(palette.paper)
            .shadow_lg()
            .child(
                div()
                    .flex()
                    .flex_col()
                    .h(px(height))
                    .child(
                        // The handle, as in the reference: drag to resize.
                        div()
                            .id("drawer-handle")
                            .flex()
                            .flex_none()
                            .items_center()
                            .justify_center()
                            .h(px(16.))
                            .cursor_row_resize()
                            .child(div().w(px(38.)).h(px(4.)).rounded(px(2.)).bg(line_strong))
                            .on_mouse_down(MouseButton::Left, cx.listener(|this, event: &MouseDownEvent, _, cx| {
                                this.resizing = Some((f32::from(event.position.y), this.drawer_height));
                                cx.notify();
                            })),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_1()
                            .min_h_0()
                            .child(
                                div()
                                    .flex()
                                    .flex_col()
                                    .flex_none()
                                    .w(px(112.))
                                    .px(px(6.))
                                    .pb(px(6.))
                                    .gap(px(2.))
                                    .border_r_1()
                                    .border_color(line)
                                    .children(rail),
                            )
                            .child(
                                div()
                                    .relative()
                                    .flex()
                                    .flex_col()
                                    .flex_1()
                                    .min_w_0()
                                    .child(
                                        div()
                                            .id("transcript")
                                            .flex()
                                            .flex_col()
                                            .flex_1()
                                            .min_h_0()
                                            .overflow_y_scroll()
                                            .track_scroll(&self.transcript)
                                            .px_3()
                                            .pb_2()
                                            .gap_4()
                                            .when(turns.is_empty(), |this| this.child(div().pt_2().text_color(palette.muted).child(t("写下问题或修改要求，模型的思考过程和结果显示在这里。"))))
                                            .children(turns)
                                            .on_scroll_wheel(cx.listener(|this, _, _, _| this.follow_transcript = false)),
                                    )
                                    .child(
                                        div()
                                            .flex()
                                            .flex_col()
                                            .flex_none()
                                            .mx_2()
                                            .mb_2()
                                            .p(px(9.))
                                            .gap(px(6.))
                                            .rounded(px(11.))
                                            .border_1()
                                            .border_color(line_strong)
                                            .bg(palette.surface)
                                            .child(div().w_full().h(px(input_height)).child(self.input.clone()))
                                            .child(
                                                div()
                                                    .flex()
                                                    .items_center()
                                                    .gap_2()
                                                    .child(div().flex_1().min_w_0().overflow_hidden().whitespace_nowrap().font_family(MONO).text_size(px(11.)).text_color(palette.muted).child(selection.unwrap_or_default()))
                                                    .when(self.suggestion().is_some() && !can_send, |this| {
                                                        this.child(div().flex_none().px(px(6.)).rounded(px(5.)).border_1().border_color(line).text_size(px(11.)).text_color(palette.muted).child(t("Tab 采用建议")))
                                                    })
                                                    .child(
                                                        div()
                                                            .id("model-picker")
                                                            .role(Role::Button)
                                                            .aria_label(t("模型后端与模型"))
                                                            .flex()
                                                            .min_w_0()
                                                            .items_center()
                                                            .gap(px(4.))
                                                            .h(px(26.))
                                                            .px_2()
                                                            .rounded(px(13.))
                                                            .cursor_pointer()
                                                            .text_size(px(12.))
                                                            .text_color(ink)
                                                            .hover(move |style| style.bg(hover))
                                                            .child(div().min_w_0().overflow_hidden().whitespace_nowrap().child(self.model_name()))
                                                            .child(icon("icons/chevrons-up-down.svg", 12., palette.muted))
                                                            .on_click(cx.listener(|this, _, _, cx| this.toggle_picker(cx))),
                                                    )
                                                    .when(offers_effort, |this| {
                                                        this.child(
                                                            div()
                                                                .id("effort-picker")
                                                                .role(Role::Button)
                                                                .aria_label(t("选择 Effort"))
                                                                .flex()
                                                                .flex_none()
                                                                .items_center()
                                                                .gap(px(4.))
                                                                .h(px(26.))
                                                                .px_2()
                                                                .rounded(px(13.))
                                                                .cursor_pointer()
                                                                .text_size(px(12.))
                                                                .text_color(ink)
                                                                .hover(move |style| style.bg(hover))
                                                                .child(if self.effort.is_empty() { t("默认").to_owned() } else { self.effort.clone() })
                                                                .child(icon("icons/chevrons-up-down.svg", 12., palette.muted))
                                                                .on_click(cx.listener(|this, _, _, cx| this.toggle_effort_picker(cx))),
                                                        )
                                                    })
                                                    .child(match running {
                                                        Some(id) => div()
                                                            .id("stop-run")
                                                            .role(Role::Button)
                                                            .aria_label(t("中断"))
                                                            .flex()
                                                            .flex_none()
                                                            .items_center()
                                                            .justify_center()
                                                            .size(px(28.))
                                                            .rounded(px(14.))
                                                            .bg(ink)
                                                            .cursor_pointer()
                                                            .child(icon("icons/square.svg", 12., palette.paper))
                                                            .on_click(cx.listener(move |this, _, _, cx| this.interrupt(id.clone(), cx))),
                                                        None => div()
                                                            .id("send-run")
                                                            .role(Role::Button)
                                                            .aria_label(t("提交任务"))
                                                            .flex()
                                                            .flex_none()
                                                            .items_center()
                                                            .justify_center()
                                                            .size(px(28.))
                                                            .rounded(px(14.))
                                                            .bg(if can_send { accent } else { line_strong })
                                                            .when(can_send, |this| this.cursor_pointer())
                                                            .child(icon("icons/arrow-up.svg", 15., accent_ink))
                                                            .on_click(cx.listener(|this, _, _, cx| this.submit(cx))),
                                                    }),
                                            ),
                                    )
                                    .when(picker_open, |this| this.child(self.picker_view(palette, cx)))
                                    .when(effort_open, |this| this.child(self.effort_view(palette, cx))),
                            ),
                    ),
            )
            .with_animation(
                id,
                Animation::new(duration).with_easing(if opening { spring } else { ease_in }),
                move |element, value| {
                    let progress = if opening { value } else { 1. - value };
                    element.h(px((height * progress).max(0.) + 2.)).opacity((progress * 1.8).clamp(0., 1.))
                },
            )
    }
}

fn relative_width() -> gpui::DefiniteLength {
    gpui::relative(0.86).into()
}

/// A stable element id for a run's button. `button` takes a static id, and runs are few.
fn leak_id(prefix: &str, id: &str) -> &'static str {
    static IDS: Mutex<Option<HashMap<String, &'static str>>> = Mutex::new(None);
    let key = format!("{prefix}-{id}");
    let mut ids = IDS.lock().unwrap();
    let map = ids.get_or_insert_with(HashMap::new);
    map.entry(key.clone()).or_insert_with(|| Box::leak(key.into_boxed_str()))
}

impl Render for Workbench {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let palette = Palette::for_appearance(window.appearance());
        let total = f32::from(window.viewport_size().width);
        let left = 220.;
        let pdf_width = ((total - left - 30.) * 0.44).clamp(280., 900.);
        let scale = window.scale_factor();
        self.pdf_target = pdf::target_width((pdf_width - 28.) * self.zoom, scale);
        self.queue_pdf_render(cx);
        let body: AnyElement = match self.tab {
            Tab::Source => self.source_view(&palette).into_any_element(),
            Tab::Review => self.review_view(&palette, cx).into_any_element(),
            Tab::Workflow => self.workflow_view(&palette).into_any_element(),
            Tab::Evidence => self.evidence_view(&palette).into_any_element(),
        };
        let max_drawer = (f32::from(window.viewport_size().height) - 240.).max(DRAWER_MIN);
        // The centre column less its margins, the drawer's mode rail and the transcript's padding.
        self.graph_width = (total - left - pdf_width - 26. - 14. - 113. - 24.).max(NODE_WIDTH);
        let graph_width = self.graph_width;
        for graph in self.turns.iter_mut().filter_map(|turn| turn.graph.as_mut()) {
            graph.fit(graph_width);
        }
        div()
            .id("workbench")
            .relative()
            .flex()
            .flex_col()
            .flex_1()
            .min_h_0()
            .on_mouse_move(cx.listener(move |this, event: &MouseMoveEvent, _, cx| {
                if let Some((start, height)) = this.resizing {
                    this.drawer_height = (height + start - f32::from(event.position.y)).clamp(DRAWER_MIN, max_drawer);
                    cx.notify();
                }
                if this.node_drag.is_some() && event.pressed_button != Some(MouseButton::Left) {
                    // The button came up outside the window.
                    this.node_drag = None;
                    cx.notify();
                }
                if let Some(drag) = this.node_drag.as_mut() {
                    let (dx, dy) = (f32::from(event.position.x) - drag.start.0, f32::from(event.position.y) - drag.start.1);
                    if drag.moved || dx.abs().max(dy.abs()) >= 4. {
                        drag.moved = true;
                        let (run, node, x, y) = (drag.run.clone(), drag.node, drag.origin.0 + dx, drag.origin.1 + dy);
                        if let Some(graph) = this.turns.iter_mut().find(|turn| turn.id == run).and_then(|turn| turn.graph.as_mut()) {
                            graph.drag_to(node, x, y);
                        }
                        cx.notify();
                    }
                }
            }))
            .on_mouse_up(MouseButton::Left, cx.listener(|this, _: &MouseUpEvent, _, cx| {
                if this.resizing.take().is_some() {
                    cx.notify();
                }
                if let Some(drag) = this.node_drag.take() {
                    this.suppress_node_click = drag.moved;
                    cx.notify();
                }
            }))
            .child(
                div()
                    .flex()
                    .flex_1()
                    .min_h_0()
                    .gap(px(6.))
                    .px(px(6.))
                    .child(
                        div()
                            .flex()
                            .flex_col()
                            .flex_none()
                            .w(px(left))
                            .rounded(px(crate::SHEET))
                            .border_1()
                            .border_color(palette.line)
                            .bg(palette.surface)
                            .overflow_hidden()
                            .child(
                                div()
                                    .flex()
                                    .flex_none()
                                    .items_center()
                                    .h(px(44.))
                                    .px_3()
                                    .text_size(px(12.))
                                    .text_color(palette.muted)
                                    .border_b_1()
                                    .border_color(palette.line)
                                    .child(t("项目文件")),
                            )
                            .child(self.file_tree(&palette, cx)),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_col()
                            .flex_1()
                            .min_w_0()
                            .rounded(px(crate::SHEET))
                            .border_1()
                            .border_color(palette.line)
                            .bg(palette.surface)
                            .overflow_hidden()
                            .child(self.tabs(&palette, cx))
                            .child(div().flex().flex_col().flex_1().min_h_0().child(body))
                            .when(self.drawer != Motion::Hidden, |this| this.child(self.drawer_view(&palette, cx))),
                    )
                    .child(self.pdf_view(scale, pdf_width, &palette, cx)),
            )
            .child(
                div()
                    .flex()
                    .flex_none()
                    .items_center()
                    .gap_3()
                    .h(px(28.))
                    .px_3()
                    .text_size(px(12.))
                    .text_color(palette.ink_soft)
                    .child(div().flex_1().min_w_0().child(self.message.clone()))
                    .child(div().font_family(MONO).text_size(px(11.)).text_color(palette.muted).child(format!("{} · {}", short(&self.project.revision, 8), short(&self.project.state_revision, 8)))),
            )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn provider_summaries_stay_whole_and_notes_join_into_lines() {
        let events = vec![
            json!({"type": "status", "data": {"status": "running"}}),
            json!({"type": "thinking", "data": {"text": "**Checking lemma 2**\n\nDetail", "source": "provider_summary"}}),
            json!({"type": "thinking", "data": {"text": "Reading sec", "source": "model_notes"}}),
            json!({"type": "thinking", "data": {"text": "tion 2\n- Found the gap\n", "source": "model_notes"}}),
        ];
        assert_eq!(thinking_lines(&events), ["Checking lemma 2", "Reading section 2", "Found the gap"]);
    }

    #[test]
    fn the_latest_suggestion_is_the_models_own_words() {
        assert_eq!(suggested_reply(&[json!({"type": "status", "data": {}})]), None);
        let events = vec![
            json!({"type": "suggested_reply", "data": {"text": "first"}}),
            json!({"type": "suggested_reply", "data": {"text": "继续检查引理 2"}}),
        ];
        assert_eq!(suggested_reply(&events).as_deref(), Some("继续检查引理 2"));
    }

    #[test]
    fn effort_levels_come_from_the_selected_model() {
        let entry = |id: &str, efforts: &[&str], default: Option<&str>| ModelEntry {
            id: id.to_owned(),
            name: id.to_owned(),
            efforts: efforts.iter().map(|level| (*level).to_owned()).collect(),
            default_effort: default.map(str::to_owned),
        };
        let models = [entry("a", &["low", "max", "ultra"], Some("low")), entry("b", &["low", "max"], None)];
        assert_eq!(efforts_for(&models, "a"), (vec!["low".to_owned(), "max".to_owned(), "ultra".to_owned()], Some("low".to_owned())));
        // With the CLI choosing the model, only what every model accepts is on offer.
        assert_eq!(efforts_for(&models, ""), (vec!["low".to_owned(), "max".to_owned()], None));
        assert_eq!(efforts_for(&[], ""), (Vec::new(), None));
    }

    #[test]
    fn evidence_kinds_read_as_words_and_unknown_ones_keep_their_name() {
        assert_ne!(evidence_kind("context_snapshot"), "context_snapshot");
        assert_eq!(evidence_kind("something_new"), "something_new");
    }

    #[test]
    fn only_backticked_equations_become_symbolic_checks() {
        let checks = math_checks("check x = 1 and `(x+1)**2 = x**2+2*x+1` but not `a = b = c`");
        assert_eq!(checks.len(), 1);
        assert_eq!(checks[0]["lhs"], "(x+1)**2");
        assert_eq!(checks[0]["rhs"], "x**2+2*x+1");
    }

    #[test]
    fn identifiers_are_picked_out_of_a_message() {
        assert_eq!(reference_identifiers("核验 10.1000/xyz， 和 2301.00001 以及 第 3.2 节"), ["10.1000/xyz", "2301.00001"]);
    }

    #[test]
    fn the_spring_passes_its_target_and_settles() {
        assert_eq!(spring(0.), 0.);
        assert!((1..100).any(|step| spring(step as f32 / 100.) > 1.));
        assert!((spring(1.) - 1.).abs() < 0.01);
    }

    #[test]
    fn paths_are_encoded_for_the_local_api() {
        assert_eq!(encode("sections/第一章 a.tex"), "sections%2F%E7%AC%AC%E4%B8%80%E7%AB%A0%20a.tex");
    }
}
