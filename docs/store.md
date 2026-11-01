# Project storage

Every managed project has a random opaque ID, a normal source tree, and its own Git repository. SQLite records project metadata, proposal decisions, transactions and Git snapshots. `revision` is SHA-256 of the complete source/asset content manifest; `commit` identifies the corresponding Git snapshot. Source ZIP export includes paper files and assets, without `.git` or application state.

Text saving and deletion require the exact previous file SHA-256. Creating a file uses a null previous hash. Uploading a binary asset follows the same rule. A proposal contains a full-project base revision, exact changes and a content digest. Accepting it requires its displayed digest and rejects any concurrent project change, including changes outside the proposed files. Repeating the same decision is idempotent. Rejecting a proposal writes no paper files.

Multi-file changes write an fsynced recovery journal before changing paper files, then update the Git snapshot and SQLite decision in the same application transaction. SQLite commit determines whether recovery retains the result or restores all previous files. Startup and subsequent mutations reconcile journals. Restore first saves the current sources as a Git snapshot, verifies the requested saved content, then applies the old sources as a new revision. Git history is never reset or deleted.

The supported tree contains paper source, bibliography, plain data, images, PDF and font files. Executables, hidden configuration files, path traversal, case collisions, links, junctions and hard-linked files are refused. Limits are 2 MiB per text edit, 20 MiB per asset, 200 MiB expanded project and 2,000 files. ZIP imports are validated before any source is extracted; they need at least one `.tex` file. UTF-8 text is used by the editor.

This is a single local user's managed data store. SQLite serializes mutations between processes, and an in-process lock serializes threaded operations. It does not defend against a malicious process with the same OS account racing filesystem checks. Power-loss durability is not asserted; process interruption recovery is tested. TeX compilation is a separate sandbox responsibility.
