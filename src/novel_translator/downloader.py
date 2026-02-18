# -*- coding: utf-8 -*-
"""
novel_translator.downloader

简单的网页下载器，将单页小说章节抓取并生成最小可用 EPUB，供 `TranslatorEngine` 直接读取。

注意：这是一个通用的启始实现，针对不同网站可能需要自定义解析器。
"""
from __future__ import annotations

import os
import requests
from bs4 import BeautifulSoup
from ebooklib import epub
from typing import Callable, Dict, Any
from urllib.parse import urlparse


def _fetch_url(url: str, timeout: int = 15) -> tuple[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36"
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.encoding = r.apparent_encoding
    return r.url, r.text


def _extract_main_html(html: str) -> tuple[str, str]:
    """尝试提取页面中的章节主要内容。返回 (title, html_fragment).

    优先查找 <article>，其次尝试常见类名，如 "chapter", "content", "novel"，最后回退到 body。
    """
    soup = BeautifulSoup(html, "lxml")
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "下载的章节"

    # 常见主内容容器
    selectors = [
        "article",
        "div[id*=chapter]",
        "div[class*=chapter]",
        "div[id*=content]",
        "div[class*=content]",
        "div[class*=novel]",
        "section",
    ]
    for sel in selectors:
        node = soup.select_one(sel)
        if node and node.get_text(strip=True):
            return title, str(node)

    # 回退到 body
    body = soup.find("body")
    if body:
        return title, str(body)

    return title, html


def _html_to_epub(title: str, html_fragment: str, output_path: str):
    book = epub.EpubBook()
    book.set_identifier("novel-translator-downloader")
    book.set_title(title)
    book.set_language("ja")
    book.add_author("Downloaded Chapter")

    # 创建一个章节
    c1 = epub.EpubHtml(title=title, file_name="chapter_1.xhtml", lang="ja")
    # 包裹为完整 HTML，确保 ebooklib 能处理
    c1.content = f"<html><head><meta charset=\"utf-8\"></head><body>{html_fragment}</body></html>"
    book.add_item(c1)

    # 基本导航
    book.toc = (epub.Link("chapter_1.xhtml", title, "chap1"),)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    book.spine = ["nav", c1]

    epub.write_epub(output_path, book)


def download_url_to_epub(url: str, output_epub: str) -> str:
    """下载指定 URL 并生成 EPUB，返回生成的 EPUB 路径。"""
    final_url, html = _fetch_url(url)
    title, fragment = _extract_main_html(html)
    out_dir = os.path.dirname(output_epub)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    _html_to_epub(title, fragment, output_epub)
    return output_epub


# ===== 站点处理器注册 =====
SITE_HANDLERS: Dict[str, Callable[[str, str, Dict[str, Any]], str]] = {}


def register_site_handler(key: str):
    def _decorator(fn: Callable[[str, str, Dict[str, Any]], str]):
        SITE_HANDLERS[key] = fn
        return fn
    return _decorator


def download_with_site(site_key: str, url: str, output_epub: str, options: Dict[str, Any] | None = None) -> str:
    """根据 site_key 调用对应的站点下载器。

    options 支持字段（视具体站点而定）:
      - selector: CSS 选择器以定位主内容
      - title_selector: CSS 选择器以定位标题
      - any other site-specific keys
    """
    opts = options or {}

    # 自动根据 URL 推断站点 key（若未显式传入）
    if not site_key:
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            host = ""
        # 简单域名到站点 key 映射
        domain_map = {
            "n.novelia.cc": "novelia",
            "novelia.cc": "novelia",
        }
        site_key = domain_map.get(host, "generic")

    handler = SITE_HANDLERS.get(site_key)
    if handler:
        return handler(url, output_epub, opts)

    # fallback: 通用下载器，支持通过 options.selector 覆盖默认提取
    selector = opts.get("selector")
    title_selector = opts.get("title_selector")

    final_url, html = _fetch_url(url)
    if selector or title_selector:
        soup = BeautifulSoup(html, "lxml")
        title = None
        fragment = None
        if title_selector:
            tnode = soup.select_one(title_selector)
            if tnode:
                title = tnode.get_text(strip=True)
        if selector:
            node = soup.select_one(selector)
            if node and node.get_text(strip=True):
                fragment = str(node)
        if not fragment:
            # 回退到默认提取
            title, fragment = _extract_main_html(html)
        else:
            if not title:
                title_tag = soup.find("title")
                title = title_tag.get_text(strip=True) if title_tag else "下载的章节"
        _html_to_epub(title, fragment, output_epub)
        return output_epub

    return download_url_to_epub(url, output_epub)


# 默认注册一个通用处理器（别名）
@register_site_handler("generic")
def _generic_handler(url: str, output_epub: str, options: Dict[str, Any]) -> str:
    return download_with_site("", url, output_epub, options)


@register_site_handler("novelia")
def _novelia_handler(url: str, output_epub: str, options: Dict[str, Any]) -> str:
    """针对 n.novelia.cc 的简单处理器。

    尝试一系列常见选择器以定位章节正文和标题，支持通过 options 覆盖选择器。
    """
    sel = options.get("selector")
    title_sel = options.get("title_selector")

    final_url, html = _fetch_url(url)
    soup = BeautifulSoup(html, "lxml")

    # 标题
    title = None
    if title_sel:
        tnode = soup.select_one(title_sel)
        if tnode:
            title = tnode.get_text(strip=True)
    if not title:
        tnode = soup.find("h1") or soup.find("h2")
        if tnode:
            title = tnode.get_text(strip=True)
    if not title:
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else "下载的章节"

    # 正文选择器优先级（针对 Novelia 镜像常见结构）
    selectors = [
        sel,
        "div.chapter-content",
        "div#chapter-content",
        "div#novel_honbun",
        "div[class*=chapter]",
        "div[class*=content]",
        "article",
        "div.entry-content",
    ]
    fragment = None
    for s in selectors:
        if not s:
            continue
        node = soup.select_one(s)
        if node and node.get_text(strip=True):
            fragment = str(node)
            break

    if not fragment:
        # 回退到默认提取
        title, fragment = _extract_main_html(html)

    _html_to_epub(title, fragment, output_epub)
    return output_epub


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="下载单页小说章节并生成 EPUB（供 novel_translator 使用）")
    p.add_argument("url", help="章节页面 URL")
    p.add_argument("-o", "--output", default="downloaded_chapter.epub", help="输出 EPUB 路径")
    args = p.parse_args()
    path = download_url_to_epub(args.url, args.output)
    print(f"已生成: {path}")

