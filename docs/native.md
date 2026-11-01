# GPUI native entry point

`native/` is an independent Rust application using `gpui-pre = 0.3.7` and `gpui-pre-platform = 0.3.7`. These exact framework dependencies and the window startup API were checked through the connected GitHub tool against Keel main commit `ee316400899344c9c98e93e81ff7a2c63b2ff20f`:

- Keel GPUI Cargo workspace: `experiments/keel-desktop-gpuix/Cargo.toml` at Keel commit `ee316400899344c9c98e93e81ff7a2c63b2ff20f`
- Keel GPUI application startup: `experiments/keel-desktop-gpuix/crates/keel-desktop/src/cli.rs` at the same commit

The application code is independently written. It does not embed Keel Core, alter the Keel checkout, or copy its visual system. Its two-sheet layout uses the same color tokens as the web workbench and follows the system light or dark appearance. It draws its own title bar over the Windows 11 Mica backdrop; the caption buttons use GPUI window control areas, so dragging, double-click maximize and Snap Layouts stay native. `build.rs` writes the application icon into a Windows resource without needing a resource compiler.

## Responsibilities

The GPUI window lists paper projects and shows backend/CLI authentication status. Native controls refresh the list, start the local service if necessary, create a template project, select a project and open the TypeScript workspace. The editor opens in the system browser using `http://127.0.0.1:<port>/#project=<id>`. This is deliberately a separate browser workspace, not an embedded native editor or a disguised webview. The TypeScript workspace provides source editing, PDF interaction and research flows; the Python service owns project storage, research tools, compilation and vendor CLI transport.

Native HTTP requests are restricted to a numeric loopback address and application API paths. The client establishes the same local cookie session and CSRF token as the browser and checks the runtime identity before creating projects. Responses are bounded to 4 MiB. Requests execute on GPUI's background executor so loading does not block rendering.

If the backend is already running, the desktop connects without taking ownership of that process. Otherwise it launches only `<root>/.venv/Scripts/python.exe -m lemfolio --port <port>` with `<root>/src` on PYTHONPATH and hidden helper windows. Logs are local at `data/native/backend.stdout.log` and `backend.stderr.log`. On exit the desktop stops only the process tree it started. It does not terminate a previously running backend. Active model runs interrupted by native shutdown remain reconstructable in runtime SQLite on the next startup; automatic execution continuation is unsupported.

## Build and launch

From a PowerShell terminal:

```powershell
Set-Location native
cargo check --locked
cargo build --locked
.\target\debug\lemfolio-desktop.exe
```

The workspace-local Cargo configuration statically links the Windows CRT. Build output goes to `native/target`. It makes no change to the user's global Cargo settings. `Cargo.lock` fixes the full dependency graph. An existing Rust MSVC toolchain and the normal GPUI Windows build dependencies are required; none are silently installed.

Options:

```text
--root <path>        Project root containing src/ and .venv/
--port 8765            Numeric local service port
--data <path>          Optional data directory when starting an owned service
--lang zh|en           Interface language; the default follows the system
--smoke-seconds 30     Open a real native window, then quit after the duration
```

`--smoke-seconds` is only a bounded startup test. It does not stand in for UI observation or a successful edit/compile run.

## Validation on 2026-10-30

`cargo check --locked --offline`, `cargo build --locked --offline` and `cargo test --locked --offline` succeeded with Rust 1.96.1 on Windows. The two native tests exercise rejection of non-API/HTTP-injection paths and real loopback HTTP forwarding of the local session cookie, CSRF token and POST body. The executable is `native/target/debug/lemfolio-desktop.exe`.

Computer Use observed the real native window connected to the existing port-8765 service, including its project listing and live authentication state. A real Refresh button click moved through loading and returned to the connected state. A separate port-8778 run with its own data directory started its own Python service, then an accessibility-targeted Create Project click created a managed template project that appeared in both the native list and API. This isolated test did not alter the main service's papers.

An accessibility-targeted Quit click stopped the owned port-8778 service; a subsequent HTTP probe failed to connect there while the separately owned port-8765 service remained healthy. The successful checks cover native startup, local-session transport, project creation, refresh and owned-service shutdown. The native Open Browser button uses GPUI's URL opener; the complete web editing workflow is validated separately.

## Workbench redesign on 2026-10-30

`scripts/capture_native.ps1` opened the rebuilt window against an isolated service on port 8791 and drove it through UI Automation. All seven buttons, including the three caption buttons, were found by accessible name, 新建项目 created a project, 刷新并启动服务 refreshed the list, 退出 closed the window, and the service it did not start stayed healthy. The same run passed in English with `--lang en`. 打开论文工作台 was not clicked, to avoid taking over the user's browser; the URL it opens is covered by the browser acceptance.
