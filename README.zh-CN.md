# Novel Translator（重构版）

基于 **Flutter + Rust** 的跨平台小说翻译工具，目标平台为 **Android + Desktop**。

## 项目结构

- UI：`apps/flutter_app`
- CLI：`apps/cli`
- 核心模块：`crates/*`

详细设计见：`docs/architecture.md`

## Rust 校验与测试

在仓库根目录执行：

```bash
cargo fmt --all --check
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
```

## Android（arm64-v8a）发行版 APK

在 `apps/flutter_app` 目录执行：

```bash
flutter pub get
flutter build apk --release --target-platform android-arm64 --split-per-abi
```

生成物路径：

`apps/flutter_app/build/app/outputs/flutter-apk/app-arm64-v8a-release.apk`

## 本次尝试结果

已尝试在当前环境执行 Android APK 构建，但因缺少 Flutter SDK（`flutter: command not found`）未能产出 APK。  
具备 Flutter 环境后，可按上述命令生成 arm64-v8a 发行版 APK。
