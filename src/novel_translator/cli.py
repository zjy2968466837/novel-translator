"""
novel_translator.cli - 命令行翻译入口

用法:
    python -m novel_translator input.epub -o output.epub --api-key sk-xxx
    python -m novel_translator gui                       # 启动 GUI
"""

import argparse
import sys
import os
import re

from novel_translator.engine import TranslatorEngine, TranslationConfig


def _strip_leading_xx_prefix(stem: str) -> str:
    """Remove leading short serial prefixes like '01.', 'AB-', 'Vol.1-'."""
    if not stem:
        return stem
    # Apply repeatedly so patterns like "01.Vol.1-" are handled.
    s = stem.strip()
    for _ in range(3):
        new_s = re.sub(r"^\s*[A-Za-z0-9]{1,12}[.\-_\s、．。]+", "", s)
        if new_s == s:
            break
        s = new_s.strip()
    return s or stem


def _default_output_path(input_file: str, fmt: str) -> str:
    ext = ".epub" if fmt == "epub" else ".txt"
    in_dir = os.path.dirname(input_file)
    in_stem = os.path.splitext(os.path.basename(input_file))[0]
    clean_stem = _strip_leading_xx_prefix(in_stem)
    # 添加前缀zh_以标识这是中文翻译
    prefixed_stem = f"zh_{clean_stem}"
    return os.path.join(in_dir, f"{prefixed_stem}{ext}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="novel-translator",
        description="日文轻小说 AI 翻译工具 — 将 EPUB 从日文翻译为中文",
    )
    sub = p.add_subparsers(dest="command")

    # ---- GUI 子命令 ----
    sub.add_parser("gui", help="启动图形界面")

    # ---- download 子命令 ----
    dl = sub.add_parser("download", help="按 URL 下载章节并生成 EPUB（供翻译使用）")
    dl.add_argument("url", help="章节页面 URL")
    dl.add_argument("-o", "--output", default=None, help="输出 EPUB 路径 (默认 downloaded_chapter.epub)")
    dl.add_argument("--site", default="generic", help="站点键（用于选择站点处理器），默认 generic")
    dl.add_argument("--selector", default="", help="可选：CSS 选择器以定位章节主体")
    dl.add_argument("--title-selector", default="", help="可选：CSS 选择器以定位标题")

    # ---- translate (默认) ----
    tr = sub.add_parser("translate", help="命令行翻译")
    tr.add_argument("input", help="输入 EPUB 文件路径")
    tr.add_argument("-o", "--output", default=None, help="输出文件路径 (默认 <去前缀xx的input名>.<fmt>)")
    tr.add_argument("-f", "--format", choices=["txt", "epub"], default="txt", help="输出格式 (默认 txt)")
    tr.add_argument("--api-key", required=True, help="OpenAI 兼容 API Key")
    tr.add_argument("--base-url", default="https://api.siliconflow.cn/v1", help="API 地址")
    tr.add_argument("--model", default="deepseek-ai/DeepSeek-V3.2", help="模型名称")
    tr.add_argument("--model-type", choices=["auto", "chat", "completion"], default="auto", help="模型类型")
    tr.add_argument("--glossary", default="", help="术语表 JSON 文件路径")
    tr.add_argument("--temperature", type=float, default=0.7)
    tr.add_argument("--top-p", type=float, default=0.9)
    tr.add_argument("--max-tokens", type=int, default=8192)
    tr.add_argument("--chunk-size", type=int, default=1500, help="分块字符数 (0=整章翻译)")
    tr.add_argument("--context-lines", type=int, default=5, help="前文上下文注入行数 (0=关闭)")
    tr.add_argument("--workers", type=int, default=1, help="并发线程数")
    tr.add_argument("--start", type=int, default=0, help="起始章节 (1-based)")
    tr.add_argument("--end", type=int, default=0, help="结束章节 (0=全部)")
    tr.add_argument("--no-checkpoint", action="store_true", help="禁用断点续传")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "gui":
        from novel_translator.gui import run_gui
        run_gui()
        return

    if args.command == "download":
        from novel_translator.downloader import download_with_site
        out = args.output or "downloaded_chapter.epub"
        opts = {}
        if args.selector:
            opts["selector"] = args.selector
        if args.title_selector:
            opts["title_selector"] = args.title_selector
        try:
            path = download_with_site(args.site, args.url, out, opts)
            print(f"✅ 已生成: {path}")
            sys.exit(0)
        except Exception as e:
            print(f"❌ 下载失败: {e}")
            sys.exit(1)

    if args.command is None:
        # 无子命令时默认打印帮助
        parser.print_help()
        sys.exit(0)

    # ---- translate 子命令 ----
    input_file = args.input
    if not os.path.exists(input_file):
        print(f"❌ 输入文件不存在: {input_file}")
        sys.exit(1)

    output_file = args.output
    if not output_file:
        output_file = _default_output_path(input_file, args.format)

    cfg = TranslationConfig(
        api_key=args.api_key,
        base_url=args.base_url,
        model_name=args.model,
        model_type=args.model_type,
        temperature=args.temperature,
        top_p=args.top_p,
        max_tokens=args.max_tokens,
        chunk_size=args.chunk_size,
        context_lines=args.context_lines,
        concurrent_workers=args.workers,
        input_file=input_file,
        output_file=output_file,
        output_format=args.format,
        glossary_file=args.glossary,
        start_chapter=args.start,
        end_chapter=args.end,
        enable_checkpoint=not args.no_checkpoint,
    )

    engine = TranslatorEngine(cfg)
    engine.on_log = lambda msg: print(msg)

    print(f"📖 输入: {input_file}")
    print(f"💾 输出: {output_file} ({args.format.upper()})")
    print()

    # 同步运行翻译
    thread = engine.start_translation()
    thread.join()

    if engine.progress.is_cancelled:
        print("\n❌ 翻译已取消")
        sys.exit(1)
    elif not engine.progress.is_running and engine.progress.translated_chars > 0:
        print(f"\n✅ 翻译完成！共 {engine.progress.translated_chars} 字")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
