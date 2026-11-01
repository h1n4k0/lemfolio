# Small paired Research Harness benchmark

This reproducible acceptance experiment follows the source conversation's suggestion to compare ordinary source context with Lemfolio's specialized Research Harness. It uses two fixed synthetic editing tasks: synchronize an equation label and all cross-file references, and repair a missing citation key using an existing synthetic BibTeX entry. The fixture explicitly describes that entry as synthetic; no publication is invented as a real source.

## Protocol

`evals/tasks.json` fixes source bytes, user intent, permitted changed files and exact expected replacements. Each task runs once per arm using the same pinned Codex model (`gpt-6.1-sol` by default), official CLI subscription transport, edit mode, output schema, source project bytes, no-tool security configuration and 120-second wall-clock ceiling.

- Baseline: complete ordinary source-file context.
- Harness: the same source files through `select_context`, plus the edit workflow, structural relationships, source locations and an independently computed static verification report.

Both small fixtures fit completely in the context budget. The order alternates baseline/harness for the first task and harness/baseline for the second. This limits a simple first-arm ordering pattern; it does not control provider caching or randomness. The runtime does not currently persist provider token counts, so the experiment equalizes the wall-clock ceiling and reports context bytes; it makes no claim of equal consumed tokens or measured cost.

The runner creates all projects under a new `data/benchmarks/<timestamp>` directory, never the production project store. It deliberately accepts model proposals only in that isolated evaluation store so independent checks can observe their effects. It asserts that files remain unchanged before digest-bound proposal acceptance. The application-owned Docker compiler executes exported bytes with network disabled and shell escape disabled.

## Independent metrics

- Exact target edit, allowing CRLF/LF normalization while retaining original output digests.
- Undefined references and citation keys from the source verifier.
- Real Docker compilation status and hash-bound PDF/log/SyncTeX receipts.
- Changed file count and changed line count.
- Changes outside the permitted files and diff lines that remain after comparing with the exact expected output.

The model's answer and `checks` strings are preserved as model output and explicitly excluded from validation evidence. Compilation and static checks do not establish mathematical or scientific correctness.

## Run and verify

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_harness.py --model gpt-6.1-sol --budget-seconds 120
```

Requires an already authenticated official Codex CLI and the built `lemfolio/tex:0.1` Docker image. The API is not involved. A new timestamped evaluation store is used on every invocation. `--output` can retain separate reports.

The runner writes its report to `reports/harness-benchmark.json`, which is not committed. It records the fixture SHA-256, runner SHA-256, exact model and CLI version, context and source manifests, original model results, proposal digests, independent checks, compile image/artifact hashes and descriptive paired outcomes. Each `receipt_sha256` hashes the corresponding object *without its own receipt field*, serialized with sorted keys, UTF-8, `ensure_ascii=False` and separators `(',', ':')`. Per-run receipts are also stored in the isolated timestamp directory.

Two tasks with one sample per arm are an execution and acceptance check. They cannot support a statistical conclusion that the Harness is better than ordinary Codex, nor represent a comparison with Codex's full interactive tool harness. Larger randomized, repeated research tasks and human review remain necessary for that claim.

## Observed result on 2026-10-30

All four real official Codex CLI runs completed using `gpt-6.1-sol`. Baseline and Harness each completed both tasks exactly, both generated compilable papers, both ended with zero unresolved references/citations, and neither introduced unrelated diff lines. The observed task-quality metrics were tied. The extra structured context did not improve these already simple tasks.

The report preserves runtime generation elapsed time and context byte counts. Elapsed generation time excludes the subsequent independent Docker compilation. With one sample per condition, timing differences do not establish a performance or token-efficiency result.

Report/result/source/artifact hashes were independently recomputed after execution. The fixture/runner hashes match the saved source files. The report's compile receipts name the immutable Docker image and concrete PDF/log/SyncTeX artifact hashes; model-written checks are not included in these independent results.
