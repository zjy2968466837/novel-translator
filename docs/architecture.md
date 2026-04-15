# Architecture

## Goals
- Full rewrite to Flutter + Rust for Android + Desktop.
- Keep translation core capabilities while removing DeepSeek Beta features.
- Keep only two provider presets: DeepSeek Official and OpenAI Compatible.

## Workspace layout
- `apps/flutter_app`: UI shell and task UX.
- `apps/cli`: batch translation and downloader commands.
- `crates/core_engine`: orchestrates status machine and module pipeline.
- `crates/api_client`: unified reqwest client and retry policy.
- `crates/api_debug`: request/response capture, redaction, zip export.
- `crates/epub_pipeline`: parse/segment/rebuild/validate EPUB stages.
- `crates/correction_retry`: quality checks + staged retry policy.
- `crates/glossary_context`: glossary loading and prompt composition.
- `crates/storage`: SQLite metadata and atomic file write-back.
- `crates/bridge`: Flutter bridge entrypoints.

## Task state machine
- `Pending`
- `Running`
- `Retrying`
- `Paused`
- `Done`
- `Failed`

Engine persists state to SQLite per chapter and task.

## Translation flow
1. Parse input EPUB.
2. Build prompt with glossary + context.
3. Call provider API.
4. Capture request/response debug log.
5. Run quality checks.
6. Apply staged retry policy when needed.
7. Write translated chapter.
8. Rebuild and validate output EPUB.
9. Atomic write output and persist checkpoint.

## Provider policy
Only presets:
- `deepseek_official` => `https://api.deepseek.com`
- `openai_compatible` => user-defined `base_url`

Removed intentionally:
- DeepSeek Beta
- Prefix completion beta
- FIM beta
