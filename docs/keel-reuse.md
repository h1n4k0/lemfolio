# Keel components reused in Lemfolio

The repository owner explicitly requested direct reuse from their Keel repository during Lemfolio development on 2026-10-30. Lemfolio is an independent project; the entire Keel Core is not imported.

Source: `src/keel/files.py` in the Keel repository, which is private.

- Fixed source commit: `ee316400899344c9c98e93e81ff7a2c63b2ff20f`.
- Source Git blob: `b0d773d3639141fad05020a8d6b144bbcb67e584`.
- Retrieved through the connected GitHub tool with the exact commit ref, not from the stale local checkout.
- Destination: `src/lemfolio/_vendor/keel_files.py`.
- Reused unchanged definitions: `sha256`, `_is_link`, `Read`, `read_stable`, `_oserror`.
- Imports were reduced to the standard library dependencies of those definitions; the required `REPARSE_POINT` constant was retained. No function or class body was changed.

`Store` calls the reused `sha256` for content hashes and revisions, `_is_link` for symlink/reparse-point checks, and `read_stable` for actual source and journal reads. Lemfolio wraps the stable reader with inode, link-count and post-read path checks. These wrappers belong to Lemfolio and do not alter the vendored implementation.

Keel's `FileAdapter`, policy, action state, artifact units, approval kernel and runtime were not copied: their dependencies and semantics differ from mutable paper transactions. Lemfolio implements project transactions, proposals, Git history and rollback itself. This reuse does not assert that Keel Runtime is ready or that Lemfolio's transactions inherit Keel's acceptance results.

## Rust diff model

Source: `experiments/keel-desktop-gpuix/crates/keel-semantic/src/diff.rs` in the Keel repository, at the same fixed commit `ee316400899344c9c98e93e81ff7a2c63b2ff20f`.

- Source Git blob: `3fe0fc3be7c44a00bbe9a9341d24e71fd3f90d29`.
- Destination: `rust/lemfolio-diff/src/keel_diff.rs`, the complete upstream file including its two original tests, unchanged.
- Reused model and algorithms: `parse_unified`, intraline spans, `DFile` / `DLine` / `DRow`, and `DiffDoc` context-gap collapsing.
- `rust/lemfolio-diff/src/lib.rs` is Lemfolio's separate adapter: it normalizes plain unified file headers and omitted hunk counts, protects body lines resembling headers, checks malformed/oversized input, and serializes the actual Keel model to JSON. It does not replace the upstream engine with a Python imitation.
- Byte ranges from the Rust engine are retained, with explicit UTF-16 positions added for TypeScript consumers. Unicode filenames and CJK/emoji intraline ranges are covered by extra adapter tests.
- `src/lemfolio/diff_engine.py` invokes `target/diff/release/lemfolio-diff.exe` with a bounded JSON request. It reports `unavailable` when the binary is absent or fails; a plain unified diff remains available separately for review.
- `cargo test --manifest-path rust/lemfolio-diff/Cargo.toml`: seven tests passed (two upstream, five Lemfolio adapter tests). The release CLI was built and exercised through the Python bridge.

The source files did not include standalone copyright/license headers. Attribution and the owner's explicit permission are recorded here; this is not a declaration of a new license for Keel or permission to distribute unrelated Keel code.
