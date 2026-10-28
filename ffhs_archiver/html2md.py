"""HTML -> Markdown conversion tuned for Moodle content (standard library only).

The converter builds a small DOM with ``html.parser`` (tolerant of the messy HTML that
Moodle editors produce) and renders it to Markdown that reads well in Obsidian and in
AI tools: headings, paragraphs, nested lists, GitHub tables, links, images, quotes,
code, embedded videos (as links) and form inputs (for quiz reviews).

Callbacks let the caller rewrite links and images, e.g. to point at files that were
downloaded into the archive.
"""

from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser
from collections.abc import Callable, Iterable
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr",
}
DROP_TAGS = {
    "script", "style", "noscript", "template", "head", "title", "meta", "link", "svg",
    "button", "canvas", "map", "colgroup", "col", "track", "param", "base",
}
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "body", "caption", "center", "dd", "details",
    "dialog", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1",
    "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "html", "legend", "li", "main",
    "menu", "nav", "ol", "p", "pre", "section", "summary", "table", "tbody", "td", "tfoot",
    "th", "thead", "tr", "ul", "iframe", "video", "audio", "object", "embed", "#root",
}
# Start tags that implicitly close an open <p> (HTML "p in button scope").
P_CLOSERS = {
    "address", "article", "aside", "blockquote", "center", "details", "dialog", "div", "dl",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hgroup", "hr", "main", "menu", "nav", "ol", "p", "pre", "section", "summary",
    "table", "ul",
}
P_SCOPE_BOUNDARY = {"html", "table", "td", "th", "caption", "button", "object", "template", "#root"}
LIST_SCOPE_BOUNDARY = {"ul", "ol", "menu", "table", "td", "th", "caption", "blockquote", "#root"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
DEFAULT_SKIP_CLASSES = {"accesshide", "sr-only", "visually-hidden", "questionflag", "skip-block",
                        "skip", "hidden-print-only"}
BR = "\x00BR\x00"


class Node:
    """Minimal DOM node."""

    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict | None = None, parent: Node | None = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list = []
        self.parent = parent

    def get(self, name: str, default: str = "") -> str:
        value = self.attrs.get(name)
        return default if value is None else value

    @property
    def classes(self) -> set:
        return set(self.get("class").split())

    def iter(self) -> Iterable[Node]:
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.iter()

    def find_all(self, pred: Callable[[Node], bool]) -> list:
        return [n for n in self.iter() if pred(n)]

    def find(self, pred: Callable[[Node], bool]) -> Node | None:
        for n in self.iter():
            if pred(n):
                return n
        return None

    def text(self, spaced: bool = False) -> str:
        """Concatenated text. ``spaced``: separate block elements and table cells (for one-line summaries)."""
        parts = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag == "br":
                parts.append("\n")
            elif child.tag not in DROP_TAGS:
                inner = child.text(spaced)
                if spaced and (child.tag in BLOCK_TAGS or child.tag in ("td", "th", "li")):
                    inner = f" {inner} "
                parts.append(inner)
        return "".join(parts)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.tag} {self.attrs}>"


def has_class(node: Node, *names: str) -> bool:
    cls = node.classes
    return any(n in cls for n in names)


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    @property
    def cur(self) -> Node:
        return self.stack[-1]

    def _close_until(self, names: set, boundary: set, inclusive: bool = True) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            tag = self.stack[i].tag
            if tag in names:
                del self.stack[i if inclusive else i + 1:]
                return
            if tag in boundary:
                return

    def _implicit_close(self, tag: str) -> None:
        if tag in P_CLOSERS:
            self._close_until({"p"}, P_SCOPE_BOUNDARY)
        if tag == "li":
            self._close_until({"li"}, LIST_SCOPE_BOUNDARY)
        elif tag in ("dt", "dd"):
            self._close_until({"dt", "dd"}, {"dl", "table", "#root"})
        elif tag == "tr":
            self._close_until({"tr"}, {"table", "thead", "tbody", "tfoot", "#root"})
        elif tag in ("td", "th"):
            self._close_until({"td", "th"}, {"tr", "table", "#root"})
        elif tag in ("thead", "tbody", "tfoot"):
            self._close_until({"thead", "tbody", "tfoot"}, {"table", "#root"})
        elif tag == "option":
            self._close_until({"option"}, {"select", "#root"})
        if tag in HEADINGS and self.cur.tag in HEADINGS:
            self.stack.pop()

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self._implicit_close(tag)
        node = Node(tag, {k.lower(): (v if v is not None else "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        self._implicit_close(tag)
        node = Node(tag, {k.lower(): (v if v is not None else "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in VOID_TAGS:
            if tag == "br":  # browsers treat </br> as <br>
                self.cur.children.append(Node("br", {}, self.cur))
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        children = self.cur.children
        if children and isinstance(children[-1], str):
            children[-1] += data
        else:
            children.append(data)


def parse_html(text: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(text or "")
    builder.close()
    return builder.root


# --------------------------------------------------------------------------- helpers

_WS = re.compile(r"[ \t\r\n\f\v]+")
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)


def normalize_space(text: str) -> str:
    text = text.replace("\xa0", " ").translate(_INVISIBLE)
    return _WS.sub(" ", text)


def html_to_text(fragment: str) -> str:
    """Plain text of an HTML fragment (for titles, availability notes, short labels)."""
    if not fragment:
        return ""
    if "<" not in fragment and "&" not in fragment:
        return normalize_space(fragment).strip()
    root = parse_html(fragment)
    return normalize_space(root.text(spaced=True)).strip()


def escape_md(text: str) -> str:
    text = re.sub(r"([*`])", r"\\\1", text)
    text = re.sub(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])", r"\\_", text)
    text = text.replace("](", "]\\(")
    text = re.sub(r"<(?=[A-Za-z/!?])", r"\\<", text)
    return text


_LINE_START = re.compile(r"^(\s*)([#>+=|]|-(?=\s|$)|\d+[.)](?=\s|$))", re.M)


def _escape_line_starts(text: str) -> str:
    return _LINE_START.sub(lambda m: m.group(1) + "\\" + m.group(2), text)


_MD_URL_SAFE = ":/?#[]@!$&'*+,;=%~"


def md_url(url: str) -> str:
    """Make a URL/path safe inside Markdown link parentheses."""
    return quote(url, safe=_MD_URL_SAFE)


def code_span(text: str) -> str:
    text = normalize_space(text).strip()
    if not text:
        return ""
    ticks = "`"
    while ticks in text:
        ticks += "`"
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def indent(text: str, prefix: str) -> str:
    return "\n".join((prefix + line) if line.strip() else line for line in text.split("\n"))


VIDEO_PROVIDERS = (
    (re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:embed|shorts|v)/|youtu\.be/)([\w-]{6,})"), "YouTube",
     lambda m: f"https://www.youtube.com/watch?v={m.group(1)}"),
    (re.compile(r"youtube\.com/watch\?.*?v=([\w-]{6,})"), "YouTube", lambda m: None),
    (re.compile(r"player\.vimeo\.com/video/(\d+)"), "Vimeo", lambda m: f"https://vimeo.com/{m.group(1)}"),
    (re.compile(r"vimeo\.com/(\d+)"), "Vimeo", lambda m: None),
    (re.compile(r"tube\.switch\.ch/(?:embed|videos)/([\w-]+)"), "SWITCHtube",
     lambda m: f"https://tube.switch.ch/videos/{m.group(1)}"),
    (re.compile(r"kaltura|switchcast|cast\.switch\.ch"), "Kaltura/SWITCHcast", lambda m: None),
    (re.compile(r"opencast|/paella/"), "Opencast", lambda m: None),
    (re.compile(r"microsoftstream\.com|sharepoint\.com/.*(?:stream|video)"), "Microsoft Stream", lambda m: None),
    (re.compile(r"/h5p/embed\.php|/mod/h5pactivity/"), "H5P", lambda m: None),
    (re.compile(r"/mod/hvp/"), "H5P", lambda m: None),
)


def classify_media_url(url: str) -> tuple[str, str]:
    """Return (provider, canonical_url) for known video/interactive platforms, else ('', url)."""
    for pattern, provider, canon in VIDEO_PROVIDERS:
        m = pattern.search(url)
        if m:
            return provider, canon(m) or url
    return "", url


def is_video_filename(name: str) -> bool:
    return bool(re.search(r"\.(mp4|m4v|mov|webm|avi|mkv|wmv|mpe?g|ogv|3gp)$", name.lower()))


def is_audio_filename(name: str) -> bool:
    return bool(re.search(r"\.(mp3|m4a|wav|ogg|oga|aac|flac|wma)$", name.lower()))


# --------------------------------------------------------------------------- converter

LinkResolver = Callable[[str], str | None]
FileResolver = Callable[[str, str, str], str | None]  # (absolute url, label, kind) -> local path
EmbedCallback = Callable[[str, str, str], None]  # (url, title, provider)


class Converter:
    """Render HTML to Markdown.

    resolve_link(url) -> replacement target (e.g. relative path to a local .md) or None
    resolve_file(url, label, kind) -> local relative path for an image/media file or None
    on_embed(url, title, provider) -> notified for iframes and external media
    """

    def __init__(
        self,
        base_url: str = "",
        resolve_link: LinkResolver | None = None,
        resolve_file: FileResolver | None = None,
        on_embed: EmbedCallback | None = None,
        heading_offset: int = 0,
        skip_classes: set | None = None,
        icon_titles: bool = False,
        is_site_url: Callable[[str], bool] | None = None,
    ) -> None:
        self.base_url = base_url
        self.resolve_link = resolve_link
        self.resolve_file = resolve_file
        self.on_embed = on_embed
        self.heading_offset = heading_offset
        self.skip_classes = DEFAULT_SKIP_CLASSES if skip_classes is None else skip_classes
        self.icon_titles = icon_titles
        self.is_site_url = is_site_url or (lambda url: False)
        self._block_cache: dict = {}

    # ---- public
    def convert(self, html_text: str) -> str:
        if not html_text or not html_text.strip():
            return ""
        root = parse_html(html_text)
        return self.render(root)

    def render(self, node: Node) -> str:
        blocks = self._blocks(node)
        return finalize("\n\n".join(b for b in blocks if b.strip()))

    def render_inline(self, node: Node) -> str:
        return self._finish_inline(self._inline_children(node))

    # ---- classification
    def _skip(self, node: Node) -> bool:
        if node.tag in DROP_TAGS:
            return True
        if node.tag == "input" and node.get("type", "").lower() == "hidden":
            return True
        cls = node.classes
        if cls & self.skip_classes:
            return True
        if node.get("aria-hidden") == "true" and node.tag in ("i", "span") and not self.icon_titles:
            return True
        if "hidden" in node.attrs and node.tag != "input":
            return True
        style = node.get("style").replace(" ", "").lower()
        if "display:none" in style:
            return True
        return False

    def _has_block(self, node: Node) -> bool:
        key = id(node)
        cached = self._block_cache.get(key)
        if cached is None:
            cached = any(isinstance(c, Node) and not self._skip(c) and (c.tag in BLOCK_TAGS or self._has_block(c))
                         for c in node.children)
            self._block_cache[key] = cached
        return cached

    # ---- block level
    def _blocks(self, node: Node) -> list:
        out: list = []
        inline: list = []

        def flush() -> None:
            text = self._finish_inline("".join(inline))
            inline.clear()
            if text:
                out.append(text)

        for child in node.children:
            if isinstance(child, str):
                inline.append(self._text(child))
                continue
            if self._skip(child):
                continue
            if child.tag in BLOCK_TAGS:
                flush()
                out.extend(self._block(child))
            elif self._has_block(child):
                flush()
                out.extend(self._blocks(child))
                if child.tag == "a" and child.get("href"):
                    link = self._link(child, text_override="Link")
                    if link:
                        out.append(link)
            else:
                inline.append(self._inline(child))
        flush()
        return out

    def _block(self, node: Node) -> list:
        tag = node.tag
        if tag in HEADINGS:
            level = max(1, min(6, int(tag[1]) + self.heading_offset))
            text = self._finish_inline(self._inline_children(node, flatten=True)).replace("\n", " ")
            return [f"{'#' * level} {text}"] if text else []
        if tag in ("ul", "ol", "menu"):
            return self._list(node, ordered=(tag == "ol"))
        if tag == "li":
            return self._list_items([node], ordered=False)
        if tag == "blockquote":
            inner = "\n\n".join(b for b in self._blocks(node) if b.strip())
            if not inner.strip():
                return []
            return ["\n".join("> " + line if line.strip() else ">" for line in inner.split("\n"))]
        if tag == "pre":
            raw = node.text().replace("\r\n", "\n").strip("\n")
            if not raw.strip():
                return []
            fence = "```"
            while fence in raw:
                fence += "`"
            return [f"{fence}\n{raw}\n{fence}"]
        if tag == "hr":
            return ["---"]
        if tag == "table":
            return self._table(node)
        if tag in ("thead", "tbody", "tfoot", "tr"):
            wrapper = Node("table")
            wrapper.children = [node]
            return self._table(wrapper)
        if tag in ("td", "th"):
            return self._blocks(node)
        if tag == "dl":
            return self._dl(node)
        if tag in ("dt",):
            text = self.render_inline(node)
            return [f"**{text}**"] if text else []
        if tag in ("legend", "summary", "caption"):
            text = self._finish_inline(self._inline_children(node, flatten=True))
            return [f"**{text}**"] if text else []
        if tag == "iframe":
            return self._iframe(node)
        if tag in ("video", "audio"):
            return self._media(node)
        if tag in ("object", "embed"):
            src = node.get("data") or node.get("src")
            if src:
                return self._embed_link(self._abs(src), node.get("title") or node.get("name"))
            return self._blocks(node)
        if tag == "figcaption":
            text = "\n\n".join(self._blocks(node))
            return [f"*{text}*"] if text.strip() and "\n" not in text else ([text] if text.strip() else [])
        return self._blocks(node)

    def _list(self, node: Node, ordered: bool) -> list:
        items: list = []
        for child in node.children:
            if isinstance(child, str):
                if child.strip():
                    items.append(Node("li", {}, node))
                    items[-1].children.append(child)
                continue
            if self._skip(child):
                continue
            if child.tag == "li":
                items.append(child)
            elif child.tag in ("ul", "ol") and items:
                items[-1].children.append(child)  # stray nested list: attach to previous item
            else:
                wrapper = Node("li", {}, node)
                wrapper.children.append(child)
                items.append(wrapper)
        start = 1
        if ordered:
            try:
                start = int(node.get("start") or 1)
            except ValueError:
                start = 1
        return self._list_items(items, ordered, start)

    def _list_items(self, items: list, ordered: bool, start: int = 1) -> list:
        lines: list = []
        number = start
        for li in items:
            blocks = [b for b in self._blocks(li) if b.strip()]
            if not blocks:
                continue
            marker = f"{number}." if ordered else "-"
            number += 1
            pad = " " * (len(marker) + 1)
            first = blocks[0]
            text = marker + " " + indent(first, pad)[len(pad):] if first.strip() else marker
            for block in blocks[1:]:
                sep = "\n" if re.match(r"^\s*(?:[-*+]|\d+[.)])\s", block) else "\n\n"
                text += sep + indent(block, pad)
            lines.append(text)
        return ["\n".join(lines)] if lines else []

    def _dl(self, node: Node) -> list:
        out: list = []
        for child in node.children:
            if not isinstance(child, Node) or self._skip(child):
                continue
            if child.tag == "dt":
                term = self.render_inline(child)
                if term:
                    out.append(f"**{term}**")
            elif child.tag == "dd":
                body = "\n\n".join(self._blocks(child))
                if body.strip():
                    if out and out[-1].startswith("**"):
                        out[-1] = out[-1] + "\n" + body
                    else:
                        out.append(body)
            else:
                out.extend(self._block(child) if child.tag in BLOCK_TAGS else [self.render_inline(child)])
        return out

    def _rows(self, table: Node) -> list:
        rows = []

        def walk(n: Node, in_head: bool) -> None:
            for c in n.children:
                if not isinstance(c, Node) or self._skip(c):
                    continue
                if c.tag == "tr":
                    rows.append((c, in_head))
                elif c.tag in ("thead", "tbody", "tfoot"):
                    walk(c, c.tag == "thead")
                elif c.tag == "table":
                    continue
        walk(table, False)
        return rows

    def _table(self, table: Node) -> list:
        caption = table.find(lambda n: n.tag == "caption")
        rows = self._rows(table)
        out: list = []
        if caption is not None:
            cap = self.render_inline(caption)
            if cap:
                out.append(f"**{cap}**")
        if not rows:
            return out + self._blocks(table)
        nested = any(cell.find(lambda n: n.tag == "table") for tr, _ in rows for cell in tr.children
                     if isinstance(cell, Node))
        grid: list = []
        header_flags: list = []
        for tr, in_head in rows:
            cells = []
            is_header_row = in_head
            ths = 0
            for cell in tr.children:
                if not isinstance(cell, Node) or cell.tag not in ("td", "th") or self._skip(cell):
                    continue
                if cell.tag == "th":
                    ths += 1
                content = self._cell(cell)
                cells.append(content)
                try:
                    span = max(1, min(20, int(cell.get("colspan") or 1)))
                except ValueError:
                    span = 1
                cells.extend([""] * (span - 1))
            if cells and ths == len([c for c in tr.children if isinstance(c, Node) and c.tag in ("td", "th")]):
                is_header_row = True
            if any(c.strip() for c in cells):
                grid.append(cells)
                header_flags.append(is_header_row)
        if not grid:
            return out
        width = max(len(r) for r in grid)
        layout_table = table.get("role") == "presentation" or width == 1
        if nested or layout_table:
            for tr, _ in rows:
                for cell in tr.children:
                    if isinstance(cell, Node) and cell.tag in ("td", "th"):
                        out.extend(self._blocks(cell))
            return out
        for r in grid:
            r.extend([""] * (width - len(r)))
        header, body = grid[0], grid[1:]  # GFM needs a header row; Moodle tables usually start with one
        lines = ["| " + " | ".join(header) + " |", "|" + "|".join([" --- "] * width) + "|"]
        lines += ["| " + " | ".join(r) + " |" for r in body]
        out.append("\n".join(lines))
        return out

    def _cell(self, cell: Node) -> str:
        blocks = [b for b in self._blocks(cell) if b.strip()]
        text = "<br>".join(b.replace("\n", "<br>") for b in blocks)
        return text.replace("|", "\\|").strip()

    def _iframe(self, node: Node) -> list:
        src = node.get("src") or node.get("data-src")
        if not src:
            return []
        return self._embed_link(self._abs(src), node.get("title"))

    def _embed_link(self, url: str, title: str) -> list:
        # Moodle sometimes embeds the PDF/video itself rather than a player page.
        # Only offer recognized Moodle file endpoints on the configured site to
        # the file resolver; ordinary iframe pages and external providers stay links.
        path = urlsplit(url).path
        file_endpoint = re.search(r"/(?:webservice/pluginfile|pluginfile|tokenpluginfile)\.php(?:/|$)", path)
        query_file = parse_qs(urlsplit(url).query).get("file", [])
        if file_endpoint and self.is_site_url(url) and self.resolve_file:
            file_path = query_file[0] if len(query_file) == 1 else path
            name = unquote(file_path.rsplit("/", 1)[-1])
            kind = "video" if is_video_filename(name) else "audio" if is_audio_filename(name) else "file"
            label = normalize_space(title or name).strip()
            local = self.resolve_file(url, label, kind)
            if local:
                word = {"video": "Video", "audio": "Audio"}.get(kind, "File")
                text = f"{word}: {escape_md(label)}" if label else word
                return [f"[{text}]({md_url(local)})"]
        provider, canonical = classify_media_url(url)
        title = normalize_space(title or "").strip()
        if self.on_embed:
            self.on_embed(canonical, title, provider or "embed")
        if provider in ("YouTube", "Vimeo", "SWITCHtube", "Kaltura/SWITCHcast", "Opencast", "Microsoft Stream"):
            label = f"Video ({provider})"
        elif provider == "H5P":
            label = "Contenuto interattivo H5P"
        else:
            label = "Contenuto incorporato"
        text = f"{label}: {escape_md(title)}" if title else label
        return [f"[{text}]({md_url(canonical)})"]

    def _media(self, node: Node) -> list:
        sources = []
        if node.get("src"):
            sources.append(node.get("src"))
        for s in node.find_all(lambda n: n.tag == "source"):
            if s.get("src"):
                sources.append(s.get("src"))
        fallback = normalize_space(node.text()).strip()
        title = node.get("title") or fallback
        kind = "video" if node.tag == "video" else "audio"
        out = []
        seen = set()
        for src in sources:
            url = self._abs(src)
            if url in seen:
                continue
            seen.add(url)
            name = unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or kind
            label = title or name
            local = self.resolve_file(url, label, kind) if self.resolve_file else None
            if not local and self.on_embed:
                provider, canonical = classify_media_url(url)
                self.on_embed(canonical, label, provider or kind)
            word = "Video" if kind == "video" else "Audio"
            out.append(f"[{word}: {escape_md(label)}]({md_url(local or url)})")
        if not out and fallback:
            out.append(escape_md(fallback))
        return out

    # ---- inline level
    def _text(self, text: str) -> str:
        return escape_md(normalize_space(text))

    def _inline_children(self, node: Node, flatten: bool = False) -> str:
        parts = []
        for child in node.children:
            if isinstance(child, str):
                parts.append(self._text(child))
            elif self._skip(child):
                continue
            elif flatten and (child.tag in BLOCK_TAGS or self._has_block(child)):
                parts.append(" " + self._inline_children(child, flatten=True) + " ")
            else:
                parts.append(self._inline(child))
        return "".join(parts)

    def _inline(self, node: Node) -> str:
        tag = node.tag
        if tag == "br":
            return BR
        if tag in ("strong", "b"):
            return self._wrap(self._inline_children(node), "**")
        if tag in ("em", "i", "cite", "dfn", "var"):
            if tag == "i" and (has_class(node, "icon", "fa") or any(c.startswith("fa-") for c in node.classes)):
                label = node.get("aria-label") or node.get("title")
                return f" [{escape_md(label)}]" if (self.icon_titles and label) else ""
            return self._wrap(self._inline_children(node), "*")
        if tag in ("code", "kbd", "samp", "tt"):
            return code_span(node.text())
        if tag in ("del", "s", "strike"):
            return self._wrap(self._inline_children(node), "~~")
        if tag in ("sup", "sub"):
            inner = self._inline_children(node).strip()
            return f"<{tag}>{inner}</{tag}>" if inner else ""
        if tag == "a":
            return self._link(node)
        if tag == "img":
            return self._img(node)
        if tag == "input":
            return self._input(node)
        if tag == "select":
            chosen = [o for o in node.find_all(lambda n: n.tag == "option") if "selected" in o.attrs]
            text = normalize_space(chosen[0].text()).strip() if chosen else ""
            return f" [{escape_md(text)}] " if text else " [____] "
        if tag == "textarea":
            value = normalize_space(node.text()).strip()
            return f" {escape_md(value)} " if value else ""
        if tag == "q":
            return "„" + self._inline_children(node) + "“"
        if tag in ("iframe", "video", "audio"):
            return " " + " ".join(self._block(node)) + " "
        if tag == "span" and has_class(node, "filter_mathjaxloader_equation", "nolink"):
            return self._inline_children(node)
        return self._inline_children(node)

    @staticmethod
    def _wrap(inner: str, mark: str) -> str:
        core = inner.strip()
        if not core or core.replace(BR, "").strip() == "":
            return inner
        lead = inner[: len(inner) - len(inner.lstrip())]
        trail = inner[len(inner.rstrip()):]
        if core.startswith(mark) and core.endswith(mark) and len(core) > 2 * len(mark):
            return inner
        while core.startswith(BR):
            core = core[len(BR):]
            lead += BR
        while core.endswith(BR):
            core = core[: -len(BR)]
            trail = BR + trail
        return f"{lead}{mark}{core.strip()}{mark}{trail}"

    def _abs(self, url: str) -> str:
        url = (url or "").strip()
        if self.base_url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
            return urljoin(self.base_url, url)
        return url

    def _link(self, node: Node, text_override: str | None = None) -> str:
        href = node.get("href").strip()
        inner = text_override if text_override is not None else self._inline_children(node)
        if has_class(node, "autolink", "glossary") and text_override is None:
            return inner  # Moodle auto-linking filters (glossary, activity names)
        if not href or href.lower().startswith(("javascript:", "#", "data:")):
            return inner
        url = self._abs(href)
        target = self.resolve_link(url) if self.resolve_link else None
        target = target or url
        text = inner.strip()
        if not text or text == BR:
            text = escape_md(unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or url)
        lead = inner[: len(inner) - len(inner.lstrip())] if inner else ""
        trail = inner[len(inner.rstrip()):] if inner else ""
        if text == escape_md(url) and target == url:
            return f"{lead}<{url}>{trail}"
        return f"{lead}[{text}]({md_url(target)}){trail}"

    def _img(self, node: Node) -> str:
        src = (node.get("src") or node.get("data-src")).strip()
        if not src or src.startswith("data:"):
            return ""
        alt = normalize_space(node.get("alt") or node.get("title") or "").strip()
        url = self._abs(src)
        path = urlsplit(url).path
        if "/theme/image.php" in path or "/theme/" in path and "/pix/" in path or path.endswith("/spacer.gif"):
            return ""  # Moodle theme icons
        local = self.resolve_file(url, alt, "image") if self.resolve_file else None
        alt_md = escape_md(alt).replace("[", "(").replace("]", ")")
        return f"![{alt_md}]({md_url(local or url)})"

    @staticmethod
    def _input(node: Node) -> str:
        typ = node.get("type", "text").lower()
        if typ in ("hidden", "submit", "button", "image", "reset", "file"):
            return ""
        if typ in ("radio", "checkbox"):
            return "☑ " if "checked" in node.attrs else "☐ "
        value = node.get("value")
        return f" {code_span(value)} " if value.strip() else " `____` "

    def _finish_inline(self, text: str) -> str:
        if not text:
            return ""
        text = text.replace("\n", " ")
        text = re.sub(r" {2,}", " ", text)
        text = text.replace(BR, "\n")
        lines = [line.strip() for line in text.split("\n")]
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return _escape_line_starts(text).strip()


def finalize(md: str) -> str:
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def to_markdown(html_text: str, **kwargs) -> str:
    return Converter(**kwargs).convert(html_text)


# --------------------------------------------------------------------------- quiz review

def _first(node: Node, pred) -> Node | None:
    return node.find(pred)


def quiz_question_to_markdown(html_text: str, converter: Converter, heading_level: int = 4) -> str:
    """Render one question of a quiz attempt review (Moodle question renderer markup)."""
    root = parse_html(html_text)
    que = _first(root, lambda n: has_class(n, "que")) or root
    info = _first(que, lambda n: has_class(n, "info"))
    qno = state = grade = ""
    if info is not None:
        n = _first(info, lambda x: has_class(x, "qno"))
        qno = normalize_space(n.text()).strip() if n else ""
        n = _first(info, lambda x: has_class(x, "state"))
        state = normalize_space(n.text()).strip() if n else ""
        n = _first(info, lambda x: has_class(x, "grade"))
        grade = normalize_space(n.text()).strip() if n else ""
    title = f"Domanda {qno}" if qno else "Domanda"
    if que is not root and has_class(que, "description"):
        title = "Testo informativo"
    meta = " · ".join(x for x in (state, grade) if x)
    out = [f"{'#' * heading_level} {title}" + (f" — {meta}" if meta else "")]

    old_icons = converter.icon_titles
    converter.icon_titles = True
    try:
        formulation = _first(que, lambda n: has_class(n, "formulation"))
        if formulation is None:
            body = converter.render(que)
            if body:
                out.append(body)
            return "\n\n".join(out)
        qtext = _first(formulation, lambda n: has_class(n, "qtext"))
        if qtext is not None:
            text = converter.render(qtext)
            if text:
                out.append(text)
        answer = _first(formulation, lambda n: has_class(n, "answer"))
        option_rows = []
        if answer is not None:
            option_rows = [c for c in answer.children if isinstance(c, Node) and
                           (c.classes & {"r0", "r1"} or c.find(lambda n: n.tag == "input" and
                                                                n.get("type") in ("radio", "checkbox")))]
        if option_rows:
            lines = []
            for row in option_rows:
                fb_node = _first(row, lambda n: has_class(n, "specificfeedback"))
                checked = bool(row.find(lambda n: n.tag == "input" and "checked" in n.attrs))
                label_parts = []
                for c in row.children:
                    if isinstance(c, Node) and (c is fb_node or c.tag == "input" or
                                                (c.tag == "i" and has_class(c, "icon"))):
                        continue
                    label_parts.append(c)
                holder = Node("div")
                holder.children = label_parts
                converter.icon_titles = False
                label = converter.render(holder).replace("\n", " ").strip()
                converter.icon_titles = True
                label = re.sub(r"^\\(?=\d+[.)]|[-+#>=|])", "", re.sub(r"\s{2,}", " ", label))
                cls = row.classes
                verdict = ""
                if "correct" in cls:
                    verdict = " ✓ (corretta)"
                elif "partiallycorrect" in cls:
                    verdict = " ~ (parzialmente corretta)"
                elif "incorrect" in cls:
                    verdict = " ✗ (errata)"
                line = f"- {'☑' if checked else '☐'} {label}{verdict}"
                if fb_node is not None:
                    fb = converter.render(fb_node).replace("\n", " ").strip()
                    if fb:
                        line += f"\n  - *Feedback:* {fb}"
                lines.append(line)
            out.append("\n".join(lines))
            # Anything else in the formulation besides qtext/answer (e.g. prompts) is ignored on purpose.
        else:
            rest = Node("div")
            rest.children = [c for c in formulation.children if c is not qtext]
            body = converter.render(rest)
            if body:
                out.append(body)
        outcome = _first(que, lambda n: has_class(n, "outcome"))
        if outcome is not None:
            for cls, label in (("specificfeedback", "Feedback"), ("generalfeedback", "Feedback generale"),
                               ("rightanswer", "Soluzione"), ("numpartscorrect", "Parti corrette")):
                n = _first(outcome, lambda x, c=cls: has_class(x, c))
                if n is not None:
                    txt = converter.render(n)
                    if txt:
                        out.append(f"**{label}:** {txt}" if "\n" not in txt else f"**{label}:**\n\n{txt}")
        comment = _first(que, lambda n: has_class(n, "comment"))
        if comment is not None:
            txt = converter.render(comment)
            if txt:
                out.append(f"**Commento:** {txt}")
    finally:
        converter.icon_titles = old_icons
    return "\n\n".join(out)


# --------------------------------------------------------------------------- markdown utilities

_FENCE = re.compile(r"^(```|~~~)")


def shift_headings(md: str, offset: int) -> str:
    """Demote Markdown ATX headings by ``offset`` levels (max 6), skipping fenced code."""
    if offset <= 0 or not md:
        return md
    out = []
    in_fence = False
    for line in md.split("\n"):
        if _FENCE.match(line.strip()):
            in_fence = not in_fence
        elif not in_fence:
            m = re.match(r"^(#{1,6})(\s.*)$", line)
            if m:
                level = min(6, len(m.group(1)) + offset)
                line = "#" * level + m.group(2)
        out.append(line)
    return "\n".join(out)


_REL_LINK = re.compile(r"(!?\[[^\]\n]*\]\()([^)\s]+)(\))")


def rebase_links(md: str, prefix: str) -> str:
    """Prefix relative link targets (files next to a note) with ``prefix`` (already URL-safe)."""
    if not prefix:
        return md
    prefix = prefix.rstrip("/") + "/"

    def repl(m: re.Match) -> str:
        target = m.group(2)
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith(("#", "/", "<")):
            return m.group(0)
        return m.group(1) + prefix + target + m.group(3)

    return _REL_LINK.sub(repl, md)


def unescape_name(name: str) -> str:
    """Moodle returns names through format_string(): decode entities, strip tags."""
    return html_to_text(_html.unescape(name or "")) if ("<" in (name or "")) else normalize_space(
        _html.unescape(name or "")).strip()


_REGION_MAIN_RE = re.compile(r'<(div|section|main)\b[^>]*\bid\s*=\s*["\']region-main["\'][^>]*>', re.I)
_ROLE_MAIN_RE = re.compile(r'<(div|section|main)\b[^>]*\brole\s*=\s*["\']main["\'][^>]*>', re.I)
_ACTIVITY_HEADER_RE = re.compile(
    r'<div\b[^>]*\bdata-for\s*=\s*["\']page-activity-header["\'][^>]*>.*?</div>\s*</div>\s*</div>', re.I | re.S)


def _inner_html(text: str, start: re.Match) -> str:
    """Inner HTML of the element ``start`` matched, balanced on its own tag name."""
    tag = start.group(1).lower()
    begin = start.end()
    depth = 1
    for m in re.compile(r'<(/?)%s\b[^>]*>' % tag, re.I).finditer(text, begin):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return text[begin:m.start()]
    return text[begin:]


def extract_region_main(html_text: str) -> str:
    """Inner HTML of Moodle's main content area, for pages only reachable with a login session
    (e.g. an FFHS ``ffhssyllabus`` Modulplan). Prefers the Boost ``#region-main`` container, then the
    inner ``role="main"`` content block, and drops scripts, styles, the activity header and completion
    forms. Returns "" when no main region is present, so the caller can flag the page instead."""
    if not html_text:
        return ""
    m = _REGION_MAIN_RE.search(html_text)
    inner = _inner_html(html_text, m) if m else ""
    if not inner:
        rm0 = _ROLE_MAIN_RE.search(html_text)
        if not rm0:
            return ""
        inner = _inner_html(html_text, rm0)
    else:
        rm = _ROLE_MAIN_RE.search(inner)
        if rm:
            inner = _inner_html(inner, rm)
    inner = _ACTIVITY_HEADER_RE.sub(" ", inner)
    inner = re.sub(r'<script\b.*?</script>', " ", inner, flags=re.I | re.S)
    inner = re.sub(r'<style\b.*?</style>', " ", inner, flags=re.I | re.S)
    inner = re.sub(r'<noscript\b.*?</noscript>', " ", inner, flags=re.I | re.S)
    inner = re.sub(r'<form\b[^>]*completion[^>]*>.*?</form>', " ", inner, flags=re.I | re.S)
    return inner.strip()


__all__ = [
    "Converter", "Node", "parse_html", "html_to_text", "to_markdown", "quiz_question_to_markdown",
    "shift_headings", "rebase_links", "md_url", "escape_md", "classify_media_url", "unescape_name",
    "is_video_filename", "is_audio_filename", "parse_qs", "extract_region_main",
]
