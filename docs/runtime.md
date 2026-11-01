# Runtime contract v1

Lemfolio's Research Harness owns paper context, scientific workflows and verification. `LocalRuntime` owns model transport, durable run state, ordered events, cancellation and result review. It never calls the project store and never writes a paper. A model's `changes` array becomes a version-bound proposal only when the API explicitly asks the store to create one. Accepting a runtime result records a decision; accepting a store proposal is the separate operation that writes project files.

## Providers

The default provider is the unmodified official Codex CLI. Existing CLI-managed ChatGPT authentication is reused; Lemfolio does not read or copy authentication files. Claude Code is the second subscription-oriented provider. Sign in outside Lemfolio using `codex login` or `claude auth login`, then restart or wait 30 seconds for the capabilities cache. No embedded login proxy is implemented. Installed CLIs are discovered from PATH; standard Windows npm launchers are resolved to their real executable/Node entrypoint without passing user text to a shell.

Codex runs in a fresh application-owned directory with `--ignore-user-config`, `--ignore-rules`, `--ephemeral`, `--sandbox read-only`, JSONL events and a strict output schema. Shell, unified execution, apps, plugins, hooks, browsers, computer use, image tools, code mode, agent spawning, memory and skill discovery are disabled for that process. Project instructions are disabled and no paper directory is mounted or supplied as cwd. Only the selected context JSON is sent through stdin. The read-only sandbox remains a second boundary against built-in modifications; these flags are not a claim that every future CLI version has undergone an OS-level isolation audit. A changed/unsupported CLI configuration fails instead of silently broadening permissions.

Claude runs with `--safe-mode`, an empty built-in tool list, strict empty MCP configuration, no session persistence, noninteractive denied permissions and schema-constrained streaming JSON. Neither provider is launched with a permission bypass. Inherited API-key environment variables are removed from these CLI subprocesses to keep explicit API fallback separate from CLI-managed login. Managed organization policy can still constrain vendor CLIs.

The optional API fallback calls only `https://api.openai.com/v1/responses`, has no tools, requests strict JSON output, sets `store:false`, and requires explicitly configured `LEMFOLIO_API_KEY` and `LEMFOLIO_API_MODEL`. It is never selected automatically. API credentials and raw HTTP error bodies are not persisted. Interrupting the application wait cannot undo a request already sent or quota already consumed.

Official references, checked 2026-10-30:

- [Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference)
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)

## Task and result

Required task fields are `projectId`, `intent`, `baseRevision`, `contextSnapshotId` and a `context` object. Optional fields include `mode`, `provider`, `model`, `requiredCapabilities` and `verificationPlan`. The API supplies the selected paper context, workflow description and independent checks. Submission freezes this task before the worker starts. Default mode is `chat`, which rejects any returned file changes. The supported modes are `chat`, `edit`, `math`, `literature` and `review`.

Every successful model result is exactly:

```json
{"answer":"Explanation with limitations","changes":[{"path":"main.tex","content":"complete replacement text"}],"checks":["Checks proposed or performed, with provenance"]}
```

The model's `checks` are model statements, not independent validation receipts. Local verification and compilation remain separate evidence. Responses must pass structural, path, duplicate-path, count and size checks. The store performs its own stricter policy and revision checks before a proposal can be accepted.

## Durable state and recovery

SQLite WAL stores tasks, results, result SHA-256 digests, status and numbered events in the configured runtime directory. States are `queued`, `running`, `completed`, `failed`, `interrupted`. At most two requests may be active. A data-directory lock prevents two runtime instances from independently owning these runs. `close()` interrupts owned CLI trees and releases this lock; the application should call it on shutdown.

`events(run_id, after)` returns at most 1000 events after the specified sequence. Sequence IDs are globally increasing, so clients use the last delivered sequence rather than assuming consecutive IDs per run. Only assistant text/status is retained from vendor streams; vendor init/account/tool payloads and stderr are excluded. Text streaming is bounded to 2000 events; the final structured result is always stored independently. Task size is limited to 1 MB, CLI output to 8 MB, final result to 4 MB, each changed file to 2 MB, and changes/checks to 100 each. `LEMFOLIO_RUN_TIMEOUT` defaults to 600 seconds and is clamped to 10–1800 seconds.

On Windows 10 / Server 2016 or newer, both CLI execution and subscription-authentication probes use an unnamed Job with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. `PROC_THREAD_ATTRIBUTE_JOB_LIST` assigns membership inside process creation, before child code executes; there is no create/suspend/assign crash window. The owner holds the sole non-inheritable Job handle, standard pipe handles use an explicit inheritance list, and no breakaway is enabled. Normal completion, interruption and close end the whole ordinary CreateProcess tree, including descendants after the direct CLI exits. Abrupt owner death closes the Job handle in the kernel without depending on Python `finally`, `atexit` or a cleanup subprocess. Unsupported platforms/Job admission fail closed rather than starting an unprotected CLI; the explicit API provider is unchanged.

The runtime lock serializes CLI launch/registration/guard persistence with close and cancellation. Authentication probes use the same protected boundary and a bounded close lock. A stalled pre-launch worker cannot spawn after close. A `run_guards` row records the mechanism only after successful protected creation; persistence failure immediately kills that tree. Job handles are never stored or inherited. Persisted PIDs are never used to kill arbitrary processes after a restart.

Interrupted or queued runs discovered after a restart become `interrupted` with preserved task context and one restart event, without replay. The event reports recorded `process_ownership`; legacy or pre-registration crashes have no recorded mechanism. `prior_cli_termination_confirmed:false` remains honest because startup itself has not inspected each prior process. This is distinct from the new kernel lifetime guarantee, verified through retained live process handles in regression tests. `recover` still returns explicit `unsupported` for automatic continuation. It cannot resume a CLI session, transfer approval to another provider, or prove mathematical correctness. To continue, review context and current project revision, then submit a new task.

Windows reference: [creation-time Job assignment](https://devblogs.microsoft.com/oldnewthing/20230209-00/?p=107812), [Job/handle-list API and supported versions](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute), [Job lifetime and descendant rules](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects). This handles the ordinary vendor CLI tree, not processes delegated to an external broker/service, hostile same-user code or forced shutdown of the OS. No cross-platform lifetime guarantee is claimed.

`approve` requires `decision` (`accept` or `reject`) and the exact `resultDigest` returned by `inspect`. It records the reviewed result digest and `project_files_applied:false`. Store proposal approval remains required for any actual changes.

`KeelRuntimeAdapter` reserves the same protocol and reports unavailable/unsupported. It contains no assumption about current Keel APIs or `.kel` formats. Enabling Keel requires an actual separately validated adapter and the same research acceptance suite.

## Verified status

- Unit/integration tests cover subprocess transport, event replay, cancellation of a real sleeping process, restart reconstruction, review binding, unsafe result paths, schema failures, subscription-first command construction, Claude result parsing and explicit API opt-in.
- The process-lifecycle follow-up adds real `os._exit` and external `TerminateProcess` owner failures, the native-created/pre-registration crash window, both CLI and descendant termination through retained Win32 handles, repeated `create_app`/HTTP restart without replay, normal completion/interrupt/close tree cleanup, close/admission races and fail-closed native fault cases.
- A real official Codex CLI call using the already logged-in ChatGPT account returned `LEMFOLIO_RUNTIME_OK` on 2026-10-30, run `fb016aeff3f448f49ac082b2fddf3cd0` in the application test-data directory `data/runtime-live-probe`. The transport resolves the native executable before npm wrappers. Its currently verified subprocess version is `0.165.0-alpha.1.2`; the separate PowerShell `codex` wrapper reports `0.166.0`. The initial probe did not persist its executable version, so that probe must not be attributed to the wrapper's version.
- A real generated-paper edit used `select_context`, the edit workflow and independent checks, then returned a one-file proposal. The store preserved the original before acceptance and wrote the edit only after explicit digest-bound acceptance. Receipt: `data/runtime-e2e/receipt.json`, run `ed275578b1e34181b9714e9c9fbe4a62`. Assertions normalized CRLF/LF because the model returned CRLF. This validates transport and approval behavior, not scientific correctness.
- Initial acceptance detected Claude Code 2.1.278 without an active login. Subsequent user-directed verification explicitly selected the already installed 2.1.283 executable and its existing Claude Pro login: two real adapter calls, content-bound proposal approval and independent Docker PDF/SyncTeX compilation passed. No install, version, PATH or main-service configuration changed during this verification. This does not claim the main service's default executable discovery has been switched.
- API fallback was not called with a paid credential. Missing configuration and bounded parsing behavior are tested; live API transport is not claimed.
