# Novel Translator (Rebuild)

Cross-platform novel translator rebuilt for **Android + Desktop** with Flutter + Rust.

- 中文说明：`README.zh-CN.md`

## Architecture

- UI: `apps/flutter_app`
- CLI: `apps/cli`
- Core crates: `crates/*`

See `docs/architecture.md` for full design and module responsibilities.

## Build

```bash
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
```

> Flutter SDK is required to build the Flutter app in `apps/flutter_app`.

## Provider presets

Only two presets are supported:
- `deepseek_official` (`https://api.deepseek.com`)
- `openai_compatible` (custom `base_url`)
