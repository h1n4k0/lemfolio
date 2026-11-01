//! Lemfolio's desktop application: projects, the paper workbench and the assistant, all drawn by GPUI.
//! The local Python service keeps project state, runs and builds; no browser is involved.
//! GPUI startup APIs and pinned dependencies follow Keel main ee316400899344c9c98e93e81ff7a2c63b2ff20f.
#![cfg_attr(windows, windows_subsystem = "windows")]

mod editor;
mod pdf;
mod run_view;
mod workbench;

use gpui::{
    App, AppContext, AssetSource, Bounds, Context, Entity, ExternalPaths, FontWeight, IntoElement,
    PathPromptOptions, Render, Rgba, Role, SharedString, Window, WindowAppearance,
    WindowBackgroundAppearance, WindowBounds, WindowControlArea, WindowOptions, div, img,
    prelude::*, px, rgb, rgba, size, svg,
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    borrow::Cow,
    fs::{self, File},
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex, OnceLock},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

const MAX_RESPONSE: u64 = 4 * 1024 * 1024;
const MAX_ARTIFACT: u64 = 256 * 1024 * 1024;
const MAX_IMPORT: u64 = 64 * 1024 * 1024;

/// Compiled into the executable, so the window never depends on files beside it.
struct Assets;

impl AssetSource for Assets {
    fn load(&self, path: &str) -> gpui::Result<Option<Cow<'static, [u8]>>> {
        macro_rules! icons {
            ($($name:literal),*) => {
                match path {
                    "brand/lemfolio-64.png" => Some(include_bytes!("../assets/brand/lemfolio-64.png").as_slice()),
                    $(concat!("icons/", $name, ".svg") => Some(include_bytes!(concat!("../assets/icons/", $name, ".svg")).as_slice()),)*
                    _ => None,
                }
            };
        }
        Ok(icons!(
            "folder-open", "plus", "refresh-cw", "log-out", "file-code-2", "server", "terminal", "box", "files",
            "arrow-left", "arrow-up", "book-open-text", "check", "chevron-down", "chevron-right", "chevrons-up-down",
            "clipboard-check", "code-xml", "file", "file-diff", "folder", "folder-input", "messages-square", "pen-line",
            "play", "save", "scroll-text", "shield-check", "sigma", "sparkles", "square", "workflow", "x", "zoom-in", "zoom-out"
        )
        .map(Cow::Borrowed))
    }

    fn list(&self, _path: &str) -> gpui::Result<Vec<SharedString>> {
        Ok(Vec::new())
    }
}

static ENGLISH: OnceLock<bool> = OnceLock::new();

#[cfg(windows)]
fn system_language() -> String {
    unsafe extern "system" {
        fn GetUserDefaultLocaleName(name: *mut u16, size: i32) -> i32;
    }
    let mut buffer = [0u16; 85];
    let length = unsafe { GetUserDefaultLocaleName(buffer.as_mut_ptr(), buffer.len() as i32) };
    String::from_utf16_lossy(&buffer[..(length.max(1) as usize - 1).min(buffer.len())])
}

#[cfg(not(windows))]
fn system_language() -> String {
    std::env::var("LANG").unwrap_or_default()
}

/// Chinese is the source language; any other system language gets English.
fn is_english(language: &str) -> bool {
    !language.to_lowercase().starts_with("zh")
}

const ENGLISH_TEXT: &[(&str, &str)] = &[
    ("已登录（{}）", "Signed in ({})"),
    ("未通过订阅登录，请运行 {}", "Not signed in with a subscription; run {}"),
    ("未安装官方 CLI", "Official CLI not installed"),
    ("本地服务", "Local service"),
    ("ChatGPT 订阅", "ChatGPT plan"),
    ("Claude 订阅", "Claude plan"),
    ("TeX 编译器", "TeX compiler"),
    ("Docker 镜像可用", "Docker image available"),
    ("不可用，无法编译", "Unavailable; cannot compile"),
    ("已连接本地服务", "Connected to the local service"),
    ("论文项目 {}", "Paper {}"),
    ("已创建「{}」，请在论文工作台中编辑", "Created “{}”; edit it in the paper workbench"),
    ("项目", "project"),
    ("正在连接本地服务…", "Connecting to the local service…"),
    ("正在创建项目…", "Creating project…"),
    ("正在连接并刷新…", "Connecting and refreshing…"),
    ("尚未连接本地服务", "Not connected to the local service"),
    ("入口文件 {}", "Entry file {}"),
    ("版本 {} · 状态 {}", "Version {} · State {}"),
    ("选择一个项目，或新建项目", "Choose a project or create one"),
    ("打开论文工作台", "Open paper workbench"),
    ("打开工作台", "Open workbench"),
    ("正在读取项目…", "Reading project…"),
    ("正在保存…", "Saving…"),
    ("已保存", "Saved"),
    ("保存", "Save"),
    ("保存失败，修改仍在编辑器中：{}", "Save failed; your changes are still in the editor: {}"),
    ("请先保存源码，接受提案会改写文件", "Save the source first; accepting a proposal rewrites files"),
    ("修改已应用；独立验证单独记录，仍需人工审阅", "Change applied; independent verification is recorded separately and human review is still needed"),
    ("提案已拒绝，文件未改动", "Proposal rejected; files unchanged"),
    ("最近的编译属于旧版本，请重新编译", "The latest build is from an older version; compile again"),
    ("尚未编译", "Not compiled yet"),
    ("请先保存源码，编译使用已保存版本", "Save the source first; compiling uses the saved version"),
    ("正在编译…", "Compiling…"),
    ("编译成功", "Compiled"),
    ("编译未成功：{}", "Compile did not succeed: {}"),
    ("这次编译没有产出 PDF", "This build produced no PDF"),
    ("PDF 无法显示：{}", "The PDF cannot be shown: {}"),
    ("请先保存源码，研究助手读取已保存版本", "Save the source first; the assistant reads the saved version"),
    ("已生成待审提案，文件尚未改动", "Proposal created for review; files unchanged"),
    ("已提交独立验证，结果在证据页查看", "Independent verification submitted; see the Evidence tab"),
    ("CLI 默认模型", "CLI default model"),
    ("编译引擎", "Compiler engine"),
    ("编译中…", "Compiling…"),
    ("编译", "Compile"),
    ("研究助手", "Research assistant"),
    ("{} 不是文本文件", "{} is not a text file"),
    ("入口", "Entry"),
    ("源码", "Source"),
    ("审阅", "Review"),
    ("流程", "Workflow"),
    ("证据", "Evidence"),
    ("待审批", "Pending"),
    ("已接受并应用", "Accepted and applied"),
    ("已拒绝", "Rejected"),
    ("项目已变化，这份提案基于旧版本，不能接受", "The project has changed; this proposal is based on an old version and cannot be accepted"),
    ("提案只有在你接受后才写入文件", "A proposal is written to files only when you accept it"),
    ("接受并写入", "Accept and write"),
    ("拒绝", "Reject"),
    ("还没有提案。在研究助手的语义修改模式里提出修改。", "No proposals yet. Ask for a change in the assistant's Semantic Editing mode."),
    ("正在读取科研流程…", "Reading workflow…"),
    ("历史", "Historical"),
    ("独立验证", "Independent verification"),
    ("修改后结构检查", "Post-edit structure check"),
    ("结构与引用检查", "Structure and citation check"),
    ("有限符号检查", "Finite symbolic check"),
    ("文献元数据核验", "Literature metadata check"),
    ("任务工具记录", "Task tool record"),
    ("冻结上下文", "Frozen context"),
    ("提案创建", "Proposal created"),
    ("任务被拒绝", "Task rejected"),
    ("证据按种类分开记录。模型完成不等于验收，人工结论由作者决定。", "Evidence is recorded by kind. A model finishing is not acceptance; the author decides the conclusions."),
    ("此项目还没有证据记录。", "This project has no evidence yet."),
    ("非当前版本", "Not current"),
    ("编译日志", "Build log"),
    ("缩小", "Zoom out"),
    ("放大", "Zoom in"),
    ("这次编译没有日志。", "This build has no log."),
    ("编译成功后，PDF 显示在这里。", "The PDF appears here after a successful build."),
    ("正在思考…", "Thinking…"),
    ("正在思考 · {} 步", "Thinking · {} steps"),
    ("这次运行没有返回摘要", "This run returned no summary"),
    ("摘要 · {} 条", "Summary · {} items"),
    ("正在读取工作流…", "Reading the workflow…"),
    ("工作流读取失败：{}", "Could not read the workflow: {}"),
    ("{} 条证据", "{} evidence records"),
    ("此步骤没有记录", "No record for this step"),
    ("拖动节点调整布局，点击查看记录", "Drag a node to move it; click to see its record"),
    ("恢复默认布局", "Reset layout"),
    ("输入论文", "Source"),
    ("冻结上下文", "Context"),
    ("独立科研检查", "Checks"),
    ("模型建议", "Model"),
    ("修改提案", "Proposal"),
    ("人工审批", "Approval"),
    ("隔离编译", "Build"),
    ("科研验收", "Acceptance"),
    ("未运行", "Not run"),
    ("运行中", "Running"),
    ("待审阅", "Pending review"),
    ("已通过", "Passed"),
    ("失败", "Failed"),
    ("已中断", "Interrupted"),
    ("受阻", "Blocked"),
    ("模型建议的修改，尚未写入文件", "Changes the model suggests, not written to any file"),
    ("删除此文件", "Delete this file"),
    ("整文件替换，生成提案后可与原文比对", "Replaces the whole file; create a proposal to compare it with the original"),
    ("还有 {} 行，生成提案后在审阅页查看全部", "{} more lines; create a proposal to see them all in Review"),
    ("默认", "Default"),
    ("默认 · {}", "Default · {}"),
    ("模型输出，未经验证", "Model output, not verified"),
    ("模型自述的检查（未经独立验证）", "Checks the model reports (not independently verified)"),
    ("生成提案并打开审阅", "Create proposal and open Review"),
    ("独立验证此任务", "Verify this run independently"),
    ("模型后端", "Model backend"),
    ("名称", "Name"),
    ("启动命令，例如 my-agent --json", "Start command, such as my-agent --json"),
    ("名称和启动命令都要填。", "Enter both a name and a start command."),
    ("可用", "Available"),
    ("未找到", "Not found"),
    ("移除 {}", "Remove {}"),
    ("添加自选 Harness", "Add your own harness"),
    ("添加", "Add"),
    ("任何命令行 agent 都行。Lemfolio 把提示词写到它的标准输入，从标准输出读结果 JSON。", "Any command-line agent works. Lemfolio writes the prompt to its standard input and reads a result JSON from its standard output."),
    ("它能用哪些工具，Lemfolio 限制不了。写入项目仍要你审阅接受。", "Lemfolio cannot limit which tools it uses. Writing to the project still needs your review."),
    ("模型由启动命令决定。", "The start command decides the model."),
    ("模型列表命令（可选）", "Models command (optional)"),
    ("启动命令里写 {model} 和 {effort}，选中的值会填入。模型列表命令每行输出一个模型；要报告 Effort，就输出 JSON。", "Write {model} and {effort} in the start command to receive the choice. The models command prints one model per line, or JSON to report effort levels."),
    ("选择 Effort", "Choose effort"),
    ("自选 Harness", "Your own harness"),
    ("未添加，可在研究助手的模型按钮里添加", "None added. Add one from the model button in the research assistant"),
    ("模型", "Model"),
    ("已登录", "Signed in"),
    ("未登录", "Not signed in"),
    ("未安装", "Not installed"),
    ("模型后端与模型", "Model backend and model"),
    ("中断", "Interrupt"),
    ("提交任务", "Send"),
    ("写下问题或修改要求，模型的思考过程和结果显示在这里。", "Write a question or a change. The model's reasoning and result appear here."),
    ("项目文件", "Project files"),
    ("讨论", "Discuss"),
    ("语义修改", "Edit"),
    ("数学推理", "Math"),
    ("文献", "Literature"),
    ("论文审阅", "Paper review"),
    ("就论文内容提问，不改文件", "Ask about the paper; no files change"),
    ("说明要改什么，结果进入审阅，由你决定是否写入", "Say what to change; the result goes to Review and you decide whether to write it"),
    ("说明要检查的推导。把等式写进反引号，如 `(x+1)**2 = x**2+2*x+1`，由 SymPy 独立检查", "Say which derivation to check. Put equations in backticks, like `(x+1)**2 = x**2+2*x+1`, for an independent SymPy check"),
    ("可直接写 DOI 或 arXiv 编号，会核验元数据", "Write a DOI or arXiv ID to verify its metadata"),
    ("审阅论断、假设与证据，不改文件", "Review claims, assumptions and evidence; no files change"),
    ("导入 ZIP 或 Prism 导出", "Import ZIP or Prism export"),
    ("正在导入…", "Importing…"),
    ("只能导入 .zip 文件", "Only .zip files can be imported"),
    ("已导入 {} 个项目", "Imported {} project(s)"),
    ("已导入 {} 个项目；跳过 {} 个不属于论文的文件", "Imported {} project(s); skipped {} non-paper file(s)"),
    ("导出记录了失败项目，请查看压缩包里的 export-errors.json", "The export lists failed projects; see export-errors.json in the archive"),
    ("返回项目列表", "Back to projects"),
    ("Tab 采用建议", "Tab to use suggestion"),
    ("有未保存的修改，请先保存", "There are unsaved changes; save first"),
    ("有未保存的修改。再关闭一次会丢弃它们。", "There are unsaved changes. Close again to discard them."),
    ("Lemfolio 项目与服务", "Lemfolio projects and service"),
    ("项目 · {}", "Projects · {}"),
    ("新建项目", "New project"),
    ("运行环境", "Environment"),
    ("处理中…", "Working…"),
    ("刷新并启动服务", "Refresh and start service"),
    ("退出", "Quit"),
    ("最小化", "Minimize"),
    ("最大化", "Maximize"),
    ("关闭窗口", "Close window"),
];

pub(crate) fn t(text: &'static str) -> &'static str {
    if *ENGLISH.get_or_init(|| is_english(&system_language())) {
        if let Some((_, english)) = ENGLISH_TEXT.iter().find(|(source, _)| *source == text) {
            return english;
        }
    }
    text
}

pub(crate) fn t1(text: &'static str, value: impl std::fmt::Display) -> String {
    t(text).replacen("{}", &value.to_string(), 1)
}

#[derive(Clone, Debug)]
struct Config {
    root: PathBuf,
    port: u16,
    data: Option<PathBuf>,
    smoke_seconds: Option<u64>,
}

impl Config {
    fn parse() -> Result<Self, String> {
        let mut config = Self {
            root: PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .into(),
            port: 8765,
            data: None,
            smoke_seconds: None,
        };
        let mut args = std::env::args().skip(1);
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--root" => config.root = PathBuf::from(args.next().ok_or("--root needs a path")?),
                "--port" => {
                    config.port = args
                        .next()
                        .ok_or("--port needs a value")?
                        .parse()
                        .map_err(|_| "Invalid port")?
                }
                "--data" => {
                    config.data = Some(PathBuf::from(args.next().ok_or("--data needs a path")?))
                }
                "--lang" => {
                    let language = args.next().ok_or("--lang needs a value")?;
                    let _ = ENGLISH.set(is_english(&language));
                }
                "--smoke-seconds" => {
                    config.smoke_seconds = Some(
                        args.next()
                            .ok_or("--smoke-seconds needs a value")?
                            .parse()
                            .map_err(|_| "Invalid smoke duration")?,
                    )
                }
                _ => return Err(format!("Unknown option: {arg}")),
            }
        }
        config.root = config
            .root
            .canonicalize()
            .map_err(|_| "Lemfolio root does not exist")?;
        Ok(config)
    }
    fn url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }
}

#[derive(Clone, Debug, Deserialize)]
pub(crate) struct Project {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub revision: String,
    #[serde(default)]
    pub state_revision: String,
    #[serde(default)]
    pub main_file: String,
}

#[derive(Clone, Copy, PartialEq)]
enum Indicator {
    Good,
    Attention,
    Off,
}

/// One line of real status. Text and indicator are derived only from `/api/status`.
#[derive(Clone)]
struct StatusRow {
    label: &'static str,
    value: String,
    indicator: Indicator,
}

#[derive(Default)]
struct Snapshot {
    projects: Vec<Project>,
    message: String,
    status: Vec<StatusRow>,
    connected: bool,
}

fn cli_row(status: &Value, id: &str, label: &'static str, plan: &'static str) -> StatusRow {
    let pointer = |field: &str| format!("/runtime/providers/{id}/{field}");
    let flag = |field: &str| {
        status
            .pointer(&pointer(field))
            .and_then(Value::as_bool)
            .unwrap_or(false)
    };
    if flag("authenticated") {
        StatusRow {
            label,
            value: t1("已登录（{}）", t(plan)),
            indicator: Indicator::Good,
        }
    } else if flag("installed") {
        let command = status
            .pointer(&pointer("login_command"))
            .and_then(Value::as_str)
            .unwrap_or("");
        StatusRow {
            label,
            value: t1("未通过订阅登录，请运行 {}", command),
            indicator: Indicator::Attention,
        }
    } else {
        StatusRow {
            label,
            value: t("未安装官方 CLI").into(),
            indicator: Indicator::Off,
        }
    }
}

/// Everything that is not a subscription CLI: optional, so its absence is not a fault.
fn harness_row(status: &Value) -> StatusRow {
    let names: Vec<&str> = status
        .pointer("/runtime/providers")
        .and_then(Value::as_object)
        .into_iter()
        .flatten()
        .filter(|(_, item)| matches!(item["kind"].as_str(), Some("harness" | "custom")) && item["installed"] == true)
        .filter_map(|(_, item)| item["name"].as_str())
        .collect();
    StatusRow {
        label: t("自选 Harness"),
        value: if names.is_empty() { t("未添加，可在研究助手的模型按钮里添加").into() } else { names.join(", ") },
        indicator: if names.is_empty() { Indicator::Off } else { Indicator::Good },
    }
}

fn status_rows(status: &Value, url: &str) -> Vec<StatusRow> {
    let compiler = status
        .pointer("/compiler/available")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    vec![
        StatusRow {
            label: t("本地服务"),
            value: url.to_owned(),
            indicator: Indicator::Good,
        },
        cli_row(status, "codex", "Codex", "ChatGPT 订阅"),
        cli_row(status, "claude", "Claude Code", "Claude 订阅"),
        harness_row(status),
        StatusRow {
            label: t("TeX 编译器"),
            value: if compiler {
                t("Docker 镜像可用").into()
            } else {
                t("不可用，无法编译").into()
            },
            indicator: if compiler {
                Indicator::Good
            } else {
                Indicator::Attention
            },
        },
    ]
}

fn error_detail(value: &Value) -> String {
    value["detail"]
        .as_str()
        .unwrap_or("Local API request failed")
        .chars()
        .take(500)
        .collect()
}

fn dechunk(mut raw: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::with_capacity(raw.len());
    loop {
        let end = raw.windows(2).position(|w| w == b"\r\n").ok_or("Invalid chunked response")?;
        let size = std::str::from_utf8(&raw[..end])
            .ok()
            .and_then(|line| usize::from_str_radix(line.split(';').next().unwrap_or("").trim(), 16).ok())
            .ok_or("Invalid chunk size")?;
        if size == 0 {
            return Ok(out);
        }
        let body = raw.get(end + 2..end + 2 + size).ok_or("Truncated chunked response")?;
        out.extend_from_slice(body);
        raw = raw.get(end + 2 + size + 2..).ok_or("Truncated chunked response")?;
    }
}

pub(crate) struct Backend {
    config: Config,
    child: Option<Child>,
    cookie: String,
    csrf: String,
}

impl Backend {
    fn new(config: Config) -> Self {
        Self {
            config,
            child: None,
            cookie: String::new(),
            csrf: String::new(),
        }
    }

    fn stop_owned(&mut self) {
        if let Some(mut child) = self.child.take() {
            if child.try_wait().ok().flatten().is_none() {
                #[cfg(windows)]
                {
                    use std::os::windows::process::CommandExt;
                    // Exact live child PID only. Terminate its owned compiler/CLI descendants too.
                    let _ = Command::new("taskkill")
                        .args(["/PID", &child.id().to_string(), "/T", "/F"])
                        .creation_flags(0x08000000)
                        .stdout(Stdio::null())
                        .stderr(Stdio::null())
                        .status();
                }
                let _ = child.kill();
            }
            let _ = child.wait();
        }
    }

    /// One local HTTP exchange. Returns the status line's code and the decoded body.
    fn exchange(&mut self, method: &str, path: &str, content_type: &str, body: &[u8], limit: u64, timeout: u64) -> Result<(String, Vec<u8>), String> {
        if !path.starts_with("/api/") || path.contains(['\r', '\n']) {
            return Err("Invalid local API path".into());
        }
        let addr: SocketAddr = format!("127.0.0.1:{}", self.config.port)
            .parse()
            .map_err(|_| "Invalid loopback address")?;
        let mut stream = TcpStream::connect_timeout(&addr, Duration::from_secs(3))
            .map_err(|_| "Python backend is not reachable")?;
        stream
            .set_read_timeout(Some(Duration::from_secs(timeout)))
            .map_err(|e| e.to_string())?;
        stream
            .set_write_timeout(Some(Duration::from_secs(timeout)))
            .map_err(|e| e.to_string())?;
        let head = format!(
            "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\nAccept: application/json\r\nContent-Type: {content_type}\r\nCookie: {}\r\nX-CSRF-Token: {}\r\nContent-Length: {}\r\n\r\n",
            self.config.port,
            self.cookie,
            self.csrf,
            body.len(),
        );
        stream
            .write_all(head.as_bytes())
            .and_then(|()| stream.write_all(body))
            .map_err(|_| "Could not send local API request")?;
        let mut bytes = Vec::new();
        stream
            .take(limit + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| "Could not read local API response")?;
        if bytes.len() as u64 > limit {
            return Err("Local API response exceeded its size limit".into());
        }
        let split = bytes
            .windows(4)
            .position(|w| w == b"\r\n\r\n")
            .ok_or("Invalid local HTTP response")?;
        let headers = std::str::from_utf8(&bytes[..split]).map_err(|_| "Invalid HTTP headers")?;
        let status = headers
            .lines()
            .next()
            .and_then(|line| line.split_whitespace().nth(1))
            .ok_or("Missing HTTP status")?
            .to_owned();
        let mut chunked = false;
        for line in headers.lines().skip(1) {
            let lower = line.to_ascii_lowercase();
            if lower.starts_with("set-cookie: lemfolio_session=") {
                self.cookie = line
                    .split_once(':')
                    .unwrap()
                    .1
                    .trim()
                    .split(';')
                    .next()
                    .unwrap_or("")
                    .to_owned();
            }
            chunked |= lower.starts_with("transfer-encoding:") && lower.contains("chunked");
        }
        let raw = &bytes[split + 4..];
        Ok((status, if chunked { dechunk(raw)? } else { raw.to_vec() }))
    }

    fn request(&mut self, method: &str, path: &str, body: Option<Value>) -> Result<Value, String> {
        let body = body.map(|v| v.to_string()).unwrap_or_default();
        let (status, bytes) = self.exchange(method, path, "application/json", body.as_bytes(), MAX_RESPONSE, 15)?;
        let value: Value =
            serde_json::from_slice(&bytes).map_err(|_| "Local API returned invalid JSON")?;
        if !status.starts_with('2') {
            return Err(error_detail(&value));
        }
        Ok(value)
    }

    /// Raw bytes, for build artifacts and ZIP uploads.
    fn request_bytes(&mut self, method: &str, path: &str, content_type: &str, body: &[u8]) -> Result<Vec<u8>, String> {
        let (status, bytes) = self.exchange(method, path, content_type, body, MAX_ARTIFACT, 120)?;
        if !status.starts_with('2') {
            let value: Value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
            return Err(error_detail(&value));
        }
        Ok(bytes)
    }

    fn ensure_started(&mut self) -> Result<(), String> {
        if self.request("GET", "/api/health", None).is_ok() {
            return Ok(());
        }
        if let Some(child) = self.child.as_mut() {
            if child.try_wait().map_err(|e| e.to_string())?.is_none() {
                return self.wait_ready();
            }
        }
        let python = self.config.root.join(if cfg!(windows) {
            ".venv/Scripts/python.exe"
        } else {
            ".venv/bin/python"
        });
        if !python.is_file() {
            return Err(
                "Project Python environment missing. Run the Lemfolio setup script first.".into(),
            );
        }
        let logs = self.config.root.join("data/native");
        fs::create_dir_all(&logs).map_err(|e| e.to_string())?;
        let stdout = File::create(logs.join("backend.stdout.log")).map_err(|e| e.to_string())?;
        let stderr = File::create(logs.join("backend.stderr.log")).map_err(|e| e.to_string())?;
        let mut command = Command::new(python);
        command
            .args(["-m", "lemfolio", "--port", &self.config.port.to_string()])
            .current_dir(&self.config.root)
            .env("PYTHONPATH", self.config.root.join("src"))
            .stdin(Stdio::null())
            .stdout(stdout)
            .stderr(stderr);
        if let Some(data) = &self.config.data {
            command.arg("--data").arg(data);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW, no visible helper terminal.
        }
        self.child = Some(
            command
                .spawn()
                .map_err(|e| format!("Could not start Python backend: {e}"))?,
        );
        self.wait_ready()
    }

    fn wait_ready(&mut self) -> Result<(), String> {
        let start = Instant::now();
        while start.elapsed() < Duration::from_secs(20) {
            if self.request("GET", "/api/health", None).is_ok() {
                return Ok(());
            }
            if let Some(child) = self.child.as_mut() {
                if child.try_wait().map_err(|e| e.to_string())?.is_some() {
                    return Err(
                        "Python backend exited during startup; see data/native/backend.stderr.log"
                            .into(),
                    );
                }
            }
            std::thread::sleep(Duration::from_millis(200));
        }
        Err("Python backend startup timed out".into())
    }

    fn refresh(&mut self, create: bool) -> Result<Snapshot, String> {
        self.ensure_started()?;
        let session = self.request("GET", "/api/session", None)?;
        self.csrf = session["csrf"]
            .as_str()
            .ok_or("Missing local session token")?
            .to_owned();
        let status = self.request("GET", "/api/status", None)?;
        if status.pointer("/runtime/runtime").and_then(Value::as_str) != Some("local") {
            return Err(
                "The loopback service did not identify itself as Lemfolio LocalRuntime".into(),
            );
        }
        let mut message = t("已连接本地服务").to_owned();
        if create {
            let seconds = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs();
            let project = self.request(
                "POST",
                "/api/projects",
                Some(json!({"name":t1("论文项目 {}", seconds),"template":true})),
            )?;
            message = t1("已创建「{}」，请在论文工作台中编辑", project["name"].as_str().unwrap_or(t("项目")));
        }
        let projects = serde_json::from_value(self.request("GET", "/api/projects", None)?)
            .map_err(|_| "Invalid project listing")?;
        Ok(Snapshot {
            projects,
            message,
            status: status_rows(&status, &self.config.url()),
            connected: true,
        })
    }
}

impl Backend {
    fn import_archive(&mut self, zip: &Path) -> Result<Value, String> {
        let metadata = fs::metadata(zip).map_err(|e| e.to_string())?;
        if metadata.len() > MAX_IMPORT {
            return Err("ZIP exceeds 64 MiB".into());
        }
        let bytes = fs::read(zip).map_err(|e| e.to_string())?;
        // Named "Imported paper" when the file has no usable stem, so the folder inside can name it.
        let stem = zip.file_stem().and_then(|stem| stem.to_str()).unwrap_or("Imported paper");
        let mut name = String::new();
        for byte in stem.bytes() {
            if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
                name.push(byte as char);
            } else {
                name.push_str(&format!("%{byte:02X}"));
            }
        }
        let response = self.request_bytes("POST", &format!("/api/import/archive?name={name}"), "application/zip", &bytes)?;
        serde_json::from_slice(&response).map_err(|_| "Local API returned invalid JSON".to_string())
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        // Only a process spawned by THIS native application is owned and terminated.
        self.stop_owned();
    }
}

struct Hub {
    backend: Arc<Mutex<Backend>>,
    snapshot: Snapshot,
    selected: Option<String>,
    busy: bool,
    workbench: Option<Entity<workbench::Workbench>>,
    close_warned: bool,
    pending_message: Option<String>,
}

impl Hub {
    fn new(config: Config, cx: &mut Context<Self>) -> Self {
        cx.on_app_quit(|this, _| {
            if let Ok(mut backend) = this.backend.lock() {
                backend.stop_owned();
            }
            async {}
        })
        .detach();
        let mut hub = Self {
            backend: Arc::new(Mutex::new(Backend::new(config))),
            snapshot: Snapshot {
                message: t("正在连接本地服务…").into(),
                ..Default::default()
            },
            selected: None,
            busy: false,
            workbench: None,
            close_warned: false,
            pending_message: None,
        };
        hub.refresh(false, cx);
        hub
    }

    fn refresh(&mut self, create: bool, cx: &mut Context<Self>) {
        if self.busy {
            return;
        }
        self.busy = true;
        self.snapshot.message = if create {
            t("正在创建项目…")
        } else {
            t("正在连接并刷新…")
        }
        .into();
        let backend = self.backend.clone();
        let task = cx.background_executor().spawn(async move {
            backend
                .lock()
                .map_err(|_| "Backend state unavailable".to_string())?
                .refresh(create)
        });
        cx.spawn(async move |this, cx| {
            let result = task.await;
            this.update(cx, |this, cx| {
                this.busy = false;
                match result {
                    Ok(snapshot) => {
                        this.snapshot = snapshot;
                        if let Some(message) = this.pending_message.take() {
                            this.snapshot.message = message;
                        }
                        if this.selected.is_none() {
                            this.selected = this.snapshot.projects.first().map(|p| p.id.clone());
                        }
                    }
                    Err(error) => {
                        this.snapshot.message = error;
                        this.snapshot.connected = false;
                    }
                }
                cx.notify();
            })
            .ok();
        })
        .detach();
        cx.notify();
    }

    fn open_workspace(&mut self, cx: &mut Context<Self>) {
        let Some(project) = self
            .selected
            .as_ref()
            .and_then(|id| self.snapshot.projects.iter().find(|project| &project.id == id))
            .cloned()
        else {
            return;
        };
        let backend = self.backend.clone();
        self.workbench = Some(cx.new(|cx| workbench::Workbench::new(backend, project, cx)));
        cx.notify();
    }

    fn leave_workspace(&mut self, cx: &mut Context<Self>) {
        if let Some(workbench) = &self.workbench {
            if workbench.read(cx).dirty(cx) {
                self.snapshot.message = t("有未保存的修改，请先保存").into();
                cx.notify();
                return;
            }
        }
        self.workbench = None;
        self.refresh(false, cx);
    }

    /// Dropped files and the import button both land here. A ZIP may hold several projects.
    fn import(&mut self, paths: Vec<PathBuf>, cx: &mut Context<Self>) {
        let zips: Vec<PathBuf> = paths
            .into_iter()
            .filter(|path| path.extension().is_some_and(|ext| ext.eq_ignore_ascii_case("zip")))
            .collect();
        if zips.is_empty() {
            self.snapshot.message = t("只能导入 .zip 文件").into();
            cx.notify();
            return;
        }
        self.snapshot.message = t("正在导入…").into();
        let backend = self.backend.clone();
        let task = cx.background_executor().spawn(async move {
            let mut backend = backend.lock().map_err(|_| "Backend state unavailable".to_string())?;
            let (mut count, mut skipped, mut errors) = (0, 0, false);
            for zip in zips {
                let value = backend.import_archive(&zip)?;
                count += value["projects"].as_array().map_or(0, Vec::len);
                skipped += value["skipped"].as_array().map_or(0, Vec::len);
                errors |= value["export_errors"].as_array().is_some_and(|items| !items.is_empty());
            }
            Ok::<_, String>((count, skipped, errors))
        });
        cx.spawn(async move |this, cx| {
            let result = task.await;
            this.update(cx, |this, cx| {
                this.busy = false;
                this.refresh(false, cx);
                let message = match result {
                    Ok((count, _, true)) => format!("{} {}", t1("已导入 {} 个项目", count), t("导出记录了失败项目，请查看压缩包里的 export-errors.json")),
                    Ok((count, 0, false)) => t1("已导入 {} 个项目", count),
                    Ok((count, skipped, false)) => t("已导入 {} 个项目；跳过 {} 个不属于论文的文件").replacen("{}", &count.to_string(), 1).replacen("{}", &skipped.to_string(), 1),
                    Err(error) => error,
                };
                this.pending_message = Some(message);
                cx.notify();
            })
            .ok();
        })
        .detach();
        cx.notify();
    }
}

/// Same paper-toned tokens as web/workbench.css, so both surfaces read as one product.
#[derive(Clone)]
pub(crate) struct Palette {
    pub desk: Rgba,
    pub paper: Rgba,
    pub surface: Rgba,
    pub line: Rgba,
    pub line_strong: Rgba,
    pub ink: Rgba,
    pub ink_soft: Rgba,
    pub muted: Rgba,
    pub accent: Rgba,
    pub accent_ink: Rgba,
    pub accent_soft: Rgba,
    pub good: Rgba,
    pub attention: Rgba,
    // Liquid glass for the popups. GPUI has no backdrop blur, so the look comes from a translucent
    // gradient, a bright rim, a sheen along the top and a deep shadow.
    pub glass_top: Rgba,
    pub glass_bottom: Rgba,
    pub glass_rim: Rgba,
    pub glass_sheen: Rgba,
    pub glass_glow: Rgba,
    pub glass_hover: Rgba,
    pub glass_selected: Rgba,
}

impl Palette {
    pub fn for_appearance(appearance: WindowAppearance) -> Self {
        match appearance {
            WindowAppearance::Dark | WindowAppearance::VibrantDark => Self {
                // Mostly see-through: the Mica backdrop tints the window frame and the gaps.
                desk: rgba(0x16151470),
                paper: rgb(0x1b1a18),
                surface: rgb(0x211f1d),
                line: rgb(0x34312c),
                line_strong: rgb(0x47433c),
                ink: rgb(0xe8e4da),
                ink_soft: rgb(0xbdb8ab),
                muted: rgb(0x979284),
                accent: rgb(0x79b8b0),
                accent_ink: rgb(0x10201e),
                accent_soft: rgb(0x24393a),
                good: rgb(0x7cc08f),
                attention: rgb(0xdcb35c),
                glass_top: rgba(0x45423de6),
                glass_bottom: rgba(0x201e1bee),
                glass_rim: rgba(0xffffff3a),
                glass_sheen: rgba(0xffffff24),
                glass_glow: rgba(0x79b8b01c),
                glass_hover: rgba(0xffffff14),
                glass_selected: rgba(0x79b8b048),
            },
            _ => Self {
                desk: rgba(0xefece570),
                paper: rgb(0xf7f5f0),
                surface: rgb(0xfcfbf8),
                line: rgb(0xdcd8cd),
                line_strong: rgb(0xc6c1b3),
                ink: rgb(0x1e1d1a),
                ink_soft: rgb(0x4d4a43),
                muted: rgb(0x6f6b61),
                accent: rgb(0x1d5a57),
                accent_ink: rgb(0xfbfaf7),
                accent_soft: rgb(0xdfeae7),
                good: rgb(0x24663c),
                attention: rgb(0x8a5f07),
                glass_top: rgba(0xffffffe4),
                glass_bottom: rgba(0xf1eee6ec),
                glass_rim: rgba(0xffffffd0),
                glass_sheen: rgba(0xffffff90),
                glass_glow: rgba(0x1d5a5714),
                glass_hover: rgba(0x0000000f),
                glass_selected: rgba(0x1d5a5728),
            },
        }
    }
}

pub(crate) const SANS: &str = "Keel Sans";
pub(crate) const MONO: &str = "Keel Mono";

pub(crate) fn icon(path: &'static str, size: f32, color: Rgba) -> impl IntoElement {
    svg().path(path).flex_none().size(px(size)).text_color(color)
}

pub(crate) fn button(
    id: &'static str,
    glyph: &'static str,
    label: impl Into<SharedString>,
    palette: &Palette,
    primary: bool,
) -> gpui::Stateful<gpui::Div> {
    let label = label.into();
    let (hover, focus) = (palette.line, palette.accent);
    let element = div()
        .id(id)
        .role(Role::Button)
        .aria_label(label.clone())
        .focusable()
        .tab_stop(true)
        .h(px(32.))
        .px_3()
        .flex()
        .flex_none()
        .items_center()
        .gap_2()
        .rounded(px(8.))
        .border_1()
        .cursor_pointer()
        .focus_visible(move |style| style.border_color(focus));
    if primary {
        element
            .bg(palette.accent)
            .border_color(palette.accent)
            .text_color(palette.accent_ink)
            .font_weight(FontWeight::MEDIUM)
            .hover(|style| style.opacity(0.9))
            .child(icon(glyph, 15., palette.accent_ink))
            .child(label)
    } else {
        element
            .border_color(palette.line_strong)
            .hover(move |style| style.bg(hover))
            .child(icon(glyph, 15., palette.ink_soft))
            .child(label)
    }
}

/// A caption button. The control area gives it native behavior, including Snap Layouts on
/// maximize; the click handler serves assistive technology, which does not hit-test.
fn caption_button(
    id: &'static str,
    label: &'static str,
    glyph: &'static str,
    area: WindowControlArea,
    hover: Rgba,
    hover_ink: Rgba,
    palette: &Palette,
) -> gpui::Stateful<gpui::Div> {
    div()
        .id(id)
        .role(Role::Button)
        .aria_label(t(label))
        .flex()
        .flex_none()
        .items_center()
        .justify_center()
        .w(px(46.))
        .h_full()
        .occlude()
        .window_control_area(area)
        .font_family("Segoe MDL2 Assets")
        .text_size(px(10.))
        .text_color(palette.ink_soft)
        .hover(move |style| style.bg(hover).text_color(hover_ink))
        .child(glyph)
}

pub(crate) const SHEET: f32 = 12.;

impl Render for Hub {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let palette = Palette::for_appearance(window.appearance());
        let selected = self
            .snapshot
            .projects
            .iter()
            .find(|project| self.selected.as_ref() == Some(&project.id))
            .cloned();

        let mut projects = div()
            .id("projects")
            .flex()
            .flex_col()
            .gap(px(1.))
            .px_2()
            .overflow_y_scroll()
            .flex_1();
        for project in &self.snapshot.projects {
            let id = project.id.clone();
            let active = self.selected.as_ref() == Some(&id);
            let revision: String = project.revision.chars().take(8).collect();
            projects = projects.child(
                div()
                    .id(gpui::ElementId::Name(id.clone().into()))
                    .role(Role::Button)
                    .aria_label(project.name.clone())
                    .focusable()
                    .tab_stop(true)
                    .flex()
                    .flex_none()
                    .items_center()
                    .gap_2()
                    .h(px(36.))
                    .px_2()
                    .rounded(px(8.))
                    .cursor_pointer()
                    .when(active, |row| {
                        row.bg(palette.accent_soft).font_weight(FontWeight::MEDIUM)
                    })
                    .when(!active, |row| {
                        let color = palette.line;
                        row.hover(move |style| style.bg(color))
                    })
                    .child(icon(
                        "icons/file-code-2.svg",
                        15.,
                        if active { palette.accent } else { palette.muted },
                    ))
                    .child(
                        div()
                            .flex_1()
                            .min_w_0()
                            .overflow_hidden()
                            .whitespace_nowrap()
                            .child(project.name.clone()),
                    )
                    .child(
                        div()
                            .flex_none()
                            .font_family(MONO)
                            .text_size(px(11.5))
                            .text_color(palette.muted)
                            .child(revision),
                    )
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.selected = Some(id.clone());
                        cx.notify();
                    })),
            );
        }
        let project_count = self.snapshot.projects.len();

        let mut status = div().flex().flex_col().gap_2();
        for (index, row) in self.snapshot.status.iter().enumerate() {
            let glyph = match index {
                0 => "icons/server.svg",
                1 | 2 => "icons/terminal.svg",
                _ => "icons/box.svg",
            };
            status = status.child(
                div()
                    .flex()
                    .items_center()
                    .gap_2()
                    .child(icon(glyph, 15., palette.muted))
                    .child(
                        div()
                            .w(px(104.))
                            .flex_none()
                            .text_color(palette.muted)
                            .child(row.label),
                    )
                    .child(
                        div()
                            .text_color(match row.indicator {
                                Indicator::Good => palette.good,
                                Indicator::Attention => palette.attention,
                                Indicator::Off => palette.muted,
                            })
                            .child(row.value.clone()),
                    ),
            );
        }
        if self.snapshot.status.is_empty() {
            status = status.child(div().text_color(palette.muted).child(t("尚未连接本地服务")));
        }

        let detail = match &selected {
            Some(project) => div()
                .flex()
                .flex_col()
                .gap_1()
                .child(
                    div()
                        .font_weight(FontWeight::SEMIBOLD)
                        .text_size(px(22.))
                        .text_color(palette.ink)
                        .child(project.name.clone()),
                )
                .child(
                    div()
                        .text_color(palette.muted)
                        .child(t1("入口文件 {}", &project.main_file)),
                )
                .child(
                    div()
                        .font_family(MONO)
                        .text_size(px(11.5))
                        .text_color(palette.muted)
                        .child(
                            t("版本 {} · 状态 {}")
                                .replacen("{}", &project.revision.chars().take(12).collect::<String>(), 1)
                                .replacen("{}", &project.state_revision.chars().take(12).collect::<String>(), 1),
                        ),
                ),
            None => div()
                .text_color(palette.muted)
                .child(t("选择一个项目，或新建项目")),
        };

        let open_label = if selected.is_some() {
            t("打开论文工作台")
        } else {
            t("打开工作台")
        };
        let open = button("open", "icons/folder-open.svg", open_label, &palette, true)
            .on_click(cx.listener(|this, _, _, cx| this.open_workspace(cx)));
        let import = button("import", "icons/folder-input.svg", t("导入 ZIP 或 Prism 导出"), &palette, false).on_click(cx.listener(|_, _, _, cx| {
            let chosen = cx.prompt_for_paths(PathPromptOptions {
                files: true,
                directories: false,
                multiple: true,
                prompt: None,
            });
            cx.spawn(async move |this, cx| {
                if let Ok(Ok(Some(paths))) = chosen.await {
                    this.update(cx, |this, cx| this.import(paths, cx)).ok();
                }
            })
            .detach();
        }));

        let (hover, hover_ink) = (palette.line, palette.ink);
        let maximized = window.is_maximized();
        let open_project = self.workbench.as_ref().map(|workbench| (workbench.read(cx).project.name.clone(), palette.line));
        let toolbar = self.workbench.clone().map(|workbench| workbench.update(cx, |workbench, cx| workbench.toolbar(&palette, cx)));
        let title_bar = div()
            .id("title-bar")
            .flex()
            .flex_none()
            .items_center()
            .h(px(38.))
            .window_control_area(WindowControlArea::Drag)
            .child(
                div()
                    .flex()
                    .flex_1()
                    .items_center()
                    .gap_2()
                    .pl_3()
                    .child(img("brand/lemfolio-64.png").flex_none().size(px(18.)))
                    .child(
                        div()
                            .font_weight(FontWeight::SEMIBOLD)
                            .text_size(px(14.))
                            .child("Lemfolio"),
                    )
                    .when_some(open_project, |this, (name, palette_hover)| {
                        this.child(
                            div()
                                .id("back")
                                .role(Role::Button)
                                .aria_label(t("返回项目列表"))
                                .occlude()
                                .flex()
                                .items_center()
                                .gap(px(5.))
                                .h(px(26.))
                                .ml_2()
                                .px_2()
                                .rounded(px(7.))
                                .cursor_pointer()
                                .text_color(palette.ink_soft)
                                .hover(move |style| style.bg(palette_hover))
                                .child(icon("icons/arrow-left.svg", 14., palette.ink_soft))
                                .child(name)
                                .on_click(cx.listener(|this, _, _, cx| this.leave_workspace(cx))),
                        )
                    }),
            )
            .when_some(toolbar, |this, toolbar| this.child(toolbar).child(div().w(px(8.))))
            .child(
                caption_button("minimize", "最小化", "\u{E921}", WindowControlArea::Min, hover, hover_ink, &palette)
                    .on_click(|_, window, _| window.minimize_window()),
            )
            .child(
                caption_button(
                    "maximize",
                    "最大化",
                    if maximized { "\u{E923}" } else { "\u{E922}" },
                    WindowControlArea::Max,
                    hover,
                    hover_ink,
                    &palette,
                )
                .on_click(|_, window, _| window.zoom_window()),
            )
            .child(
                caption_button("close", "关闭窗口", "\u{E8BB}", WindowControlArea::Close, rgb(0xc42b1c), rgb(0xffffff), &palette)
                    .on_click(|_, window, _| window.remove_window()),
            );

        div()
            .id("lemfolio-native")
            .role(Role::Application)
            .aria_label(t("Lemfolio 项目与服务"))
            .flex()
            .flex_col()
            .size_full()
            .bg(palette.desk)
            .text_color(palette.ink)
            .text_size(px(13.))
            .font_family(SANS)
            .on_drop(cx.listener(|this, paths: &ExternalPaths, _, cx| this.import(paths.paths().to_vec(), cx)))
            .child(title_bar)
            .when_some(self.workbench.clone(), |this, workbench| this.child(workbench))
            .when(self.workbench.is_none(), |this| this.child(
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
                            .w(px(320.))
                            .flex_none()
                            .rounded(px(SHEET))
                            .border_1()
                            .border_color(palette.line)
                            .bg(palette.surface)
                            .overflow_hidden()
                            .child(
                                div()
                                    .flex()
                                    .flex_none()
                                    .items_center()
                                    .gap_2()
                                    .h(px(40.))
                                    .px_3()
                                    .text_color(palette.muted)
                                    .text_size(px(12.))
                                    .child(icon("icons/files.svg", 14., palette.muted))
                                    .child(t1("项目 · {}", project_count)),
                            )
                            .child(projects)
                            .child(
                                div()
                                    .flex()
                                    .flex_none()
                                    .p_3()
                                    .flex_col()
                                    .gap_2()
                                    .child(button("new", "icons/plus.svg", t("新建项目"), &palette, false).on_click(
                                        cx.listener(|this, _, _, cx| this.refresh(true, cx)),
                                    ))
                                    .child(import),
                            ),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_col()
                            .flex_1()
                            .min_w_0()
                            .gap_5()
                            .p_6()
                            .rounded(px(SHEET))
                            .border_1()
                            .border_color(palette.line)
                            .bg(palette.surface)
                            .child(detail)
                            .child(div().flex().gap_2().child(open))
                            .child(
                                div()
                                    .flex()
                                    .flex_col()
                                    .gap_3()
                                    .p_4()
                                    .rounded(px(SHEET))
                                    .bg(palette.paper)
                                    .border_1()
                                    .border_color(palette.line)
                                    .child(
                                        div()
                                            .font_weight(FontWeight::MEDIUM)
                                            .text_color(palette.ink_soft)
                                            .child(t("运行环境")),
                                    )
                                    .child(status)
                                    .child(
                                        div()
                                            .flex()
                                            .gap_2()
                                            .pt_1()
                                            .child(
                                                button(
                                                    "refresh",
                                                    "icons/refresh-cw.svg",
                                                    if self.busy {
                                                        t("处理中…")
                                                    } else {
                                                        t("刷新并启动服务")
                                                    },
                                                    &palette,
                                                    false,
                                                )
                                                .on_click(cx.listener(|this, _, _, cx| {
                                                    this.refresh(false, cx)
                                                })),
                                            )
                                            .child(
                                                button("quit", "icons/log-out.svg", t("退出"), &palette, false)
                                                    .on_click(|_, _, cx| cx.quit()),
                                            ),
                                    ),
                            ),
                    ),
            ))
            .when(self.workbench.is_none() || !self.snapshot.connected || self.close_warned, |this| this.child(
                div()
                    .flex()
                    .flex_none()
                    .items_center()
                    .h(px(28.))
                    .px_3()
                    .text_size(px(12.))
                    .text_color(if self.snapshot.connected {
                        palette.ink_soft
                    } else {
                        palette.attention
                    })
                    .child(self.snapshot.message.clone()),
            ))
    }
}

fn main() {
    let config = match Config::parse() {
        Ok(value) => value,
        Err(error) => {
            eprintln!("{error}");
            return;
        }
    };
    let smoke_seconds = config.smoke_seconds;
    gpui_platform::application().with_assets(Assets).run(move |cx: &mut App| {
        cx.bind_keys(editor::bindings());
        // Chinese has no Keel glyphs and falls through to the system font, as it did with Segoe UI.
        if let Err(error) = cx.text_system().add_fonts(vec![
            Cow::Borrowed(include_bytes!("../../assets/fonts/keel/ttf/KeelSans-Regular.ttf").as_slice()),
            Cow::Borrowed(include_bytes!("../../assets/fonts/keel/ttf/KeelSans-Medium.ttf").as_slice()),
            Cow::Borrowed(include_bytes!("../../assets/fonts/keel/ttf/KeelSans-SemiBold.ttf").as_slice()),
            Cow::Borrowed(include_bytes!("../../assets/fonts/keel/ttf/KeelMono-Regular.ttf").as_slice()),
        ]) {
            eprintln!("Keel fonts did not load: {error}");
        }
        cx.on_window_closed(|cx, _| {
            if cx.windows().is_empty() {
                cx.quit();
            }
        })
        .detach();
        let bounds = Bounds::centered(None, size(px(1040.), px(680.)), cx);
        cx.open_window(
            WindowOptions {
                window_bounds: Some(WindowBounds::Windowed(bounds)),
                // The window draws its own title bar so the top edge belongs to the design.
                titlebar: Some(gpui::TitlebarOptions {
                    title: Some("Lemfolio".into()),
                    appears_transparent: true,
                    ..Default::default()
                }),
                window_background: WindowBackgroundAppearance::MicaBackdrop,
                window_min_size: Some(size(px(780.), px(500.))),
                ..Default::default()
            },
            |window, cx| {
                let hub = cx.new(|cx| Hub::new(config, cx));
                // Closing with unsaved source warns once; a second close is the author's decision.
                let guard = hub.clone();
                window.on_window_should_close(cx, move |_, cx| {
                    guard.update(cx, |hub, cx| {
                        let dirty = hub.workbench.as_ref().is_some_and(|workbench| workbench.read(cx).dirty(cx));
                        if dirty && !hub.close_warned {
                            hub.close_warned = true;
                            hub.snapshot.message = t("有未保存的修改。再关闭一次会丢弃它们。").into();
                            cx.notify();
                            return false;
                        }
                        true
                    })
                });
                hub
            },
        )
        .expect("Open GPUI window");
        if let Some(seconds) = smoke_seconds {
            cx.spawn(async move |cx| {
                cx.background_executor()
                    .timer(Duration::from_secs(seconds))
                    .await;
                cx.update(|cx| cx.quit());
            })
            .detach();
        }
        cx.activate(true);
    });
}

#[cfg(test)]
mod tests {
    /// Every message the window can show has English, with the same placeholders.
    #[test]
    fn all_messages_have_english() {
        let source = concat!(include_str!("main.rs"), include_str!("workbench.rs"), include_str!("run_view.rs"));
        let body = source.split("mod tests").next().unwrap();
        for prefix in ["t(\"", "t1(\""] {
            for piece in body.split(prefix).skip(1) {
                let message = piece.split('"').next().unwrap();
                if message.chars().any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c)) {
                    let entry = super::ENGLISH_TEXT.iter().find(|(key, _)| *key == message);
                    let (_, english) = entry.unwrap_or_else(|| panic!("no English for {message}"));
                    assert_eq!(message.matches("{}").count(), english.matches("{}").count(), "{message}");
                }
            }
        }
    }

    #[test]
    fn only_chinese_system_languages_keep_chinese() {
        for language in ["zh-CN", "zh-TW", "ZH", "zh"] {
            assert!(!super::is_english(language));
        }
        for language in ["en-US", "fr-FR", ""] {
            assert!(super::is_english(language));
        }
    }

    use super::*;
    use std::net::TcpListener;

    fn config(port: u16) -> Config {
        Config {
            root: PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .into(),
            port,
            data: None,
            smoke_seconds: None,
        }
    }

    #[test]
    fn refuses_non_api_paths_before_connecting() {
        let mut backend = Backend::new(config(1));
        assert!(
            backend
                .request("GET", "https://example.com/api/projects", None)
                .unwrap_err()
                .contains("Invalid local API path")
        );
        assert!(
            backend
                .request("GET", "/api/session\r\nInjected: value", None)
                .unwrap_err()
                .contains("Invalid local API path")
        );
    }

    #[test]
    fn session_cookie_and_csrf_are_forwarded_to_loopback_only() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            for index in 0..2 {
                let (mut stream, peer) = listener.accept().unwrap();
                assert!(peer.ip().is_loopback());
                stream
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let mut bytes = Vec::new();
                loop {
                    let mut chunk = [0u8; 2048];
                    let n = stream.read(&mut chunk).unwrap();
                    assert!(n > 0);
                    bytes.extend_from_slice(&chunk[..n]);
                    if let Some(split) = bytes.windows(4).position(|w| w == b"\r\n\r\n") {
                        let header = std::str::from_utf8(&bytes[..split]).unwrap();
                        let len: usize = header
                            .lines()
                            .find_map(|l| l.strip_prefix("Content-Length: "))
                            .unwrap()
                            .parse()
                            .unwrap();
                        if bytes.len() >= split + 4 + len {
                            break;
                        }
                    }
                }
                let text = String::from_utf8(bytes).unwrap();
                let body = if index == 0 {
                    assert!(text.starts_with("GET /api/session HTTP/1.1"));
                    "{\"csrf\":\"test-csrf\"}"
                } else {
                    assert!(text.starts_with("POST /api/projects HTTP/1.1"));
                    assert!(text.contains("Cookie: lemfolio_session=test-session\r\n"));
                    assert!(text.contains("X-CSRF-Token: test-csrf\r\n"));
                    assert!(text.ends_with("{\"name\":\"Test\"}"));
                    "{\"id\":\"test-project\"}"
                };
                let cookie = if index == 0 {
                    "Set-Cookie: lemfolio_session=test-session; HttpOnly; SameSite=strict\r\n"
                } else {
                    ""
                };
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n{}Connection: close\r\n\r\n{}",
                    body.len(),
                    cookie,
                    body
                )
                .unwrap();
            }
        });
        let mut backend = Backend::new(config(port));
        let session = backend.request("GET", "/api/session", None).unwrap();
        backend.csrf = session["csrf"].as_str().unwrap().to_owned();
        let project = backend
            .request("POST", "/api/projects", Some(json!({"name":"Test"})))
            .unwrap();
        assert_eq!(project["id"], "test-project");
        server.join().unwrap();
    }
}
