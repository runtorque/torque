"""Deterministic Torque Help documentation index and query helpers.

The Help surface is intentionally read-only and source-code backed.  It reads a
small allow-list of maintained markdown files from the installed Torque tree,
never from runtime state, board rows, journals, credentials, or arbitrary paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import re

import yaml

HELP_SCHEMA_VERSION = 1
HELP_INDEX_MODE = "runtime_markdown_allowlist"
MAX_LIMIT = 50
DEFAULT_SEARCH_LIMIT = 8
DEFAULT_SHOW_CHARS = 8000
MAX_SHOW_CHARS = 20000
DEFAULT_SNIPPET_CHARS = 700
MAX_SNIPPET_CHARS = 1400
ROOT_HELP_SOURCES = ("README.md", "AGENTS.md", "CLAUDE.md")
HELP_SOURCE_ALLOWLIST_DESCRIPTION = (
    "mkdocs.yml nav markdown under docs/ plus README.md, AGENTS.md, and CLAUDE.md"
)
HELP_SOURCE_EXCLUDES = (
    ".torque runtime state",
    "SQLite databases",
    "logs",
    "credentials/secrets",
    "agent journals/board rows unless already documented in maintained markdown",
    "arbitrary caller-provided filesystem paths",
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_./:-]*", re.IGNORECASE)
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_HTML_RE = re.compile(r"<[^>]+>")
_COMMON_TERMS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "i", "in", "is", "it", "of", "on", "or", "the", "to", "what", "when",
    "where", "with", "torque", "do", "does", "can", "use", "using",
}


class _HelpDocsYamlLoader(yaml.SafeLoader):
    """Safe mkdocs loader that ignores mkdocs plugin Python-name tags."""


def _ignore_yaml_python_name(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> None:
    if isinstance(node, yaml.ScalarNode):
        loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        loader.construct_mapping(node)
    return None


_HelpDocsYamlLoader.add_multi_constructor(
    "tag:yaml.org,2002:python/name:",
    _ignore_yaml_python_name,
)


@dataclass(frozen=True)
class HelpSection:
    id: str
    title: str
    level: int
    anchor: str
    source_path: str
    line_start: int
    line_end: int
    text: str

    @property
    def path_anchor(self) -> str:
        return f"{self.source_path}#{self.anchor}" if self.anchor else self.source_path


@dataclass(frozen=True)
class HelpTopic:
    id: str
    title: str
    summary: str
    source_path: str
    source_hash: str
    updated_at: str
    audience_tags: tuple[str, ...]
    restricted_safe: bool
    text: str
    sections: tuple[HelpSection, ...] = field(default_factory=tuple)
    nav_title: str = ""
    examples: tuple[str, ...] = field(default_factory=tuple)

    def compact_dict(self, *, include_sections: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "topic_id": self.id,
            "title": self.title,
            "summary": self.summary,
            "source_path": self.source_path,
            "source_hash": self.source_hash,
            "updated_at": self.updated_at,
            "audience_tags": list(self.audience_tags),
            "restricted_safe": self.restricted_safe,
            "examples": list(self.examples[:3]),
        }
        if include_sections:
            data["sections"] = [
                {
                    "id": section.id,
                    "title": section.title,
                    "level": section.level,
                    "anchor": section.anchor,
                    "source_path": section.source_path,
                    "path_anchor": section.path_anchor,
                    "line_start": section.line_start,
                    "line_end": section.line_end,
                }
                for section in self.sections
            ]
        return data


@dataclass(frozen=True)
class HelpIndex:
    root: Path
    topics: tuple[HelpTopic, ...]
    source_paths: tuple[str, ...]
    index_hash: str

    def source_model(self) -> dict[str, Any]:
        return {
            "mode": HELP_INDEX_MODE,
            "schema_version": HELP_SCHEMA_VERSION,
            "allowlist": HELP_SOURCE_ALLOWLIST_DESCRIPTION,
            "source_paths": list(self.source_paths),
            "excludes": list(HELP_SOURCE_EXCLUDES),
            "cache": "none; markdown is loaded from the installed tree at query time",
            "restricted_safe": True,
        }


def _repo_root(base_dir: str | Path = "") -> Path:
    if base_dir:
        candidate = Path(base_dir).expanduser().resolve()
        if (candidate / "mkdocs.yml").is_file() and (candidate / "docs").is_dir():
            return candidate
        if (candidate / "docs").is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent


def _normalize_rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _slug(text: str) -> str:
    text = _LINK_RE.sub(r"\1", str(text or ""))
    text = _HTML_RE.sub("", text)
    text = text.strip().lower()
    text = re.sub(r"[`*_~]", "", text)
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text).strip("-")
    return text or "section"


def _topic_id_for_path(rel_path: str) -> str:
    no_ext = re.sub(r"\.md$", "", rel_path, flags=re.IGNORECASE)
    return _slug(no_ext.replace("/", " "))


def _clean_inline_markdown(text: str) -> str:
    text = _LINK_RE.sub(r"\1", str(text or ""))
    text = _HTML_RE.sub("", text)
    text = re.sub(r"[`*_~]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_markdown(text: str) -> str:
    out: list[str] = []
    in_fence = False
    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            out.append(line)
            continue
        if _HEADING_RE.match(stripped):
            continue
        if not stripped:
            out.append("")
            continue
        if stripped.startswith("!["):
            continue
        cleaned = _clean_inline_markdown(stripped)
        cleaned = re.sub(r"^[-*+]\s+", "", cleaned)
        cleaned = re.sub(r"^\d+[.)]\s+", "", cleaned)
        cleaned = cleaned.strip(" |")
        if cleaned:
            out.append(cleaned)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _excerpt(text: str, *, max_chars: int = DEFAULT_SNIPPET_CHARS) -> str:
    max_chars = max(80, min(int(max_chars or DEFAULT_SNIPPET_CHARS), MAX_SNIPPET_CHARS))
    cleaned = re.sub(r"\s+", " ", _strip_markdown(text)).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    cut = cleaned[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{cut}…"


def _first_paragraph(markdown: str) -> str:
    blocks = re.split(r"\n\s*\n", str(markdown or ""))
    for block in blocks:
        cleaned = _strip_markdown(block)
        if not cleaned:
            continue
        if cleaned.startswith("---") or cleaned.startswith("|"):
            continue
        return _excerpt(cleaned, max_chars=260)
    return ""


def _file_updated_at(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
    except OSError:
        ts = 0
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _audience_tags(rel_path: str) -> tuple[str, ...]:
    tags = {"help", "restricted-agent-safe"}
    if rel_path in {"AGENTS.md", "CLAUDE.md"}:
        tags.update({"agent", "worker", "engineer", "architect", "maintainer"})
    elif rel_path == "README.md" or rel_path.startswith("docs/foundations/"):
        tags.update({"user", "operator", "agent"})
    elif rel_path.startswith("docs/team/"):
        tags.update({"user", "agent", "worker", "engineer", "architect"})
    elif rel_path.startswith("docs/tasks/"):
        tags.update({"user", "agent", "worker", "engineer"})
    elif rel_path.startswith("docs/operate/"):
        tags.update({"operator", "user", "engineer", "architect"})
    elif rel_path.startswith("docs/reference/") or rel_path in {"docs/architecture.md", "docs/roadmap.md"}:
        tags.update({"operator", "agent", "engineer", "architect", "maintainer"})
    else:
        tags.update({"user", "agent"})
    return tuple(sorted(tags))


def _collect_nav_items(nav: Any) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    if isinstance(nav, list):
        for entry in nav:
            items.extend(_collect_nav_items(entry))
    elif isinstance(nav, dict):
        for title, value in nav.items():
            if isinstance(value, str) and value.lower().endswith(".md"):
                items.append((str(title), value))
            else:
                items.extend(_collect_nav_items(value))
    return items


def _mkdocs_nav_sources(root: Path) -> list[tuple[str, Path]]:
    mkdocs_path = root / "mkdocs.yml"
    if not mkdocs_path.is_file():
        return []
    try:
        data = yaml.load(
            mkdocs_path.read_text(encoding="utf-8"),
            Loader=_HelpDocsYamlLoader,
        ) or {}
    except Exception:
        return []
    docs_dir = root / "docs"
    out: list[tuple[str, Path]] = []
    for title, nav_path in _collect_nav_items(data.get("nav", [])):
        candidate = (docs_dir / nav_path).resolve()
        try:
            candidate.relative_to(docs_dir.resolve())
        except ValueError:
            continue
        if candidate.is_file() and candidate.suffix.lower() == ".md":
            out.append((title, candidate))
    return out


def _iter_help_sources(root: Path) -> list[tuple[str, Path]]:
    sources: list[tuple[str, Path]] = []
    seen: set[str] = set()

    for rel in ROOT_HELP_SOURCES:
        path = (root / rel).resolve()
        if path.is_file():
            sources.append((Path(rel).stem, path))
            seen.add(_normalize_rel(path, root))

    for title, path in _mkdocs_nav_sources(root):
        rel = _normalize_rel(path, root)
        if rel not in seen:
            sources.append((title, path))
            seen.add(rel)

    return sources


def _extract_examples(markdown: str) -> tuple[str, ...]:
    examples: list[str] = []
    in_fence = False
    fence_lines: list[str] = []
    for raw_line in str(markdown or "").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            if in_fence:
                block = "\n".join(fence_lines).strip()
                if block and re.search(r"\b(torque|make|mcp|yaml|python|pytest|npm)\b", block, re.I):
                    examples.append(block[:500])
                fence_lines = []
            in_fence = not in_fence
            continue
        if in_fence:
            fence_lines.append(raw_line.rstrip())
            continue
        if re.match(r"^(torque|make|pytest|python3?|npm|gh)\b", stripped):
            examples.append(stripped[:240])
        if len(examples) >= 5:
            break
    deduped: list[str] = []
    seen: set[str] = set()
    for item in examples:
        key = item.casefold()
        if key not in seen:
            deduped.append(item)
            seen.add(key)
        if len(deduped) >= 5:
            break
    return tuple(deduped)


def _parse_sections(rel_path: str, markdown: str) -> tuple[HelpSection, ...]:
    lines = str(markdown or "").splitlines()
    headings: list[tuple[int, int, str, str]] = []
    in_fence = False
    used_anchors: dict[str, int] = {}
    for idx, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(stripped)
        if not match:
            continue
        level = len(match.group(1))
        title = _clean_inline_markdown(match.group(2))
        anchor_base = _slug(title)
        count = used_anchors.get(anchor_base, 0)
        used_anchors[anchor_base] = count + 1
        anchor = anchor_base if count == 0 else f"{anchor_base}-{count}"
        headings.append((idx, level, title, anchor))

    sections: list[HelpSection] = []
    for pos, (line_start, level, title, anchor) in enumerate(headings):
        next_line = headings[pos + 1][0] if pos + 1 < len(headings) else len(lines) + 1
        section_lines = lines[line_start:next_line - 1]
        text = "\n".join(section_lines).strip()
        section_id = f"{_topic_id_for_path(rel_path)}#{anchor}"
        sections.append(HelpSection(
            id=section_id,
            title=title,
            level=level,
            anchor=anchor,
            source_path=rel_path,
            line_start=line_start,
            line_end=max(line_start, next_line - 1),
            text=text,
        ))
    return tuple(sections)


def _topic_title(nav_title: str, markdown: str, rel_path: str) -> str:
    for line in str(markdown or "").splitlines():
        match = _HEADING_RE.match(line.strip())
        if match and len(match.group(1)) == 1:
            title = _clean_inline_markdown(match.group(2))
            if title:
                return title
    return str(nav_title or "").strip() or Path(rel_path).stem.replace("-", " ").title()


def build_help_index(base_dir: str | Path = "") -> HelpIndex:
    root = _repo_root(base_dir)
    topics: list[HelpTopic] = []
    for nav_title, path in _iter_help_sources(root):
        try:
            rel_path = _normalize_rel(path, root)
        except ValueError:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        source_hash = _source_hash(text)
        title = _topic_title(nav_title, text, rel_path)
        body_after_first_heading = re.sub(r"^#\s+.*?(?:\n|$)", "", text, count=1, flags=re.S)
        summary = _first_paragraph(body_after_first_heading) or _first_paragraph(text)
        sections = _parse_sections(rel_path, text)
        topics.append(HelpTopic(
            id=_topic_id_for_path(rel_path),
            title=title,
            summary=summary,
            source_path=rel_path,
            source_hash=source_hash,
            updated_at=_file_updated_at(path),
            audience_tags=_audience_tags(rel_path),
            restricted_safe=True,
            text=text,
            sections=sections,
            nav_title=nav_title,
            examples=_extract_examples(text),
        ))
    source_paths = tuple(topic.source_path for topic in topics)
    digest = hashlib.sha256()
    for topic in topics:
        digest.update(topic.source_path.encode())
        digest.update(topic.source_hash.encode())
    return HelpIndex(root=root, topics=tuple(topics), source_paths=source_paths, index_hash=digest.hexdigest()[:16])


def _normalize_limit(limit: Any, default: int = DEFAULT_SEARCH_LIMIT) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, MAX_LIMIT))


def _normalize_max_chars(value: Any, default: int = DEFAULT_SHOW_CHARS) -> int:
    try:
        chars = int(value)
    except (TypeError, ValueError):
        chars = default
    return max(200, min(chars, MAX_SHOW_CHARS))


def _tokenize(text: str) -> list[str]:
    tokens = [token.casefold() for token in _TOKEN_RE.findall(str(text or ""))]
    return [token for token in tokens if token not in _COMMON_TERMS and len(token) > 1]


def _result_dict(topic: HelpTopic, section: HelpSection | None, score: float, query: str) -> dict[str, Any]:
    target_text = section.text if section else topic.text
    title = section.title if section else topic.title
    anchor = section.anchor if section else ""
    source_path = topic.source_path
    path_anchor = f"{source_path}#{anchor}" if anchor else source_path
    return {
        "topic_id": topic.id,
        "section_id": section.id if section else "",
        "title": title,
        "topic_title": topic.title,
        "summary": topic.summary,
        "excerpt": _best_excerpt(target_text, query),
        "source_path": source_path,
        "anchor": anchor,
        "path_anchor": path_anchor,
        "source_hash": topic.source_hash,
        "updated_at": topic.updated_at,
        "audience_tags": list(topic.audience_tags),
        "restricted_safe": topic.restricted_safe,
        "score": round(score, 3),
    }


def _best_excerpt(text: str, query: str) -> str:
    stripped = _strip_markdown(text)
    if not stripped:
        return ""
    query_tokens = _tokenize(query)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", stripped) if p.strip()]
    if not paragraphs:
        return _excerpt(stripped)
    best = paragraphs[0]
    best_score = -1
    for paragraph in paragraphs:
        lower = paragraph.casefold()
        score = sum(lower.count(token) for token in query_tokens)
        if score > best_score:
            best = paragraph
            best_score = score
    return _excerpt(best)


def _score_text(query: str, title: str, text: str, source_path: str) -> float:
    q = str(query or "").strip().casefold()
    if not q:
        return 0.0
    tokens = _tokenize(q)
    if not tokens:
        tokens = [q]
    title_lower = str(title or "").casefold()
    text_lower = str(text or "").casefold()
    path_lower = str(source_path or "").casefold()
    score = 0.0
    if q in title_lower:
        score += 30.0
    if q in text_lower:
        score += 15.0
    if q in path_lower:
        score += 8.0
    unique_tokens = set(tokens)
    for token in unique_tokens:
        if token in title_lower:
            score += 10.0
        if token in path_lower:
            score += 3.0
        count = text_lower.count(token)
        if count:
            score += min(8.0, count * 1.5)
    if unique_tokens and all(token in text_lower or token in title_lower or token in path_lower for token in unique_tokens):
        score += 8.0
    return score


def list_help_topics(*, audience: str = "", base_dir: str | Path = "") -> dict[str, Any]:
    index = build_help_index(base_dir)
    audience = str(audience or "").strip().casefold()
    topics = []
    for topic in index.topics:
        if audience and audience not in {tag.casefold() for tag in topic.audience_tags}:
            continue
        topics.append(topic.compact_dict(include_sections=True))
    return {
        "type": "help_topics",
        "schema_version": HELP_SCHEMA_VERSION,
        "status": "ok",
        "topics": topics,
        "source_model": index.source_model(),
        "index_hash": index.index_hash,
    }


def _resolve_topic(index: HelpIndex, ident: str) -> tuple[HelpTopic | None, HelpSection | None]:
    raw = str(ident or "").strip()
    if not raw:
        return None, None
    raw = raw.replace("\\", "/")
    path_part, sep, anchor_part = raw.partition("#")
    normalized = path_part.strip().strip("/")
    if ".." in Path(normalized).parts:
        return None, None
    normalized_slug = _slug(normalized)
    anchor_slug = _slug(anchor_part) if sep else ""

    for topic in index.topics:
        candidates = {
            topic.id.casefold(),
            topic.source_path.casefold(),
            Path(topic.source_path).name.casefold(),
            _slug(topic.title).casefold(),
        }
        if normalized.casefold() in candidates or normalized_slug.casefold() in candidates:
            if anchor_slug:
                for section in topic.sections:
                    if anchor_slug in {section.anchor.casefold(), _slug(section.title).casefold()}:
                        return topic, section
                return topic, None
            return topic, None
        for section in topic.sections:
            if raw.casefold() in {section.id.casefold(), section.path_anchor.casefold()}:
                return topic, section
    return None, None


def show_help_topic(topic: str, *, max_chars: Any = DEFAULT_SHOW_CHARS, base_dir: str | Path = "") -> dict[str, Any]:
    index = build_help_index(base_dir)
    resolved, section = _resolve_topic(index, topic)
    if not resolved:
        return {
            "type": "help_topic",
            "schema_version": HELP_SCHEMA_VERSION,
            "status": "not_found",
            "message": "No Torque Help topic matched the requested id/path. Use help_list or help_search to discover valid topics.",
            "requested": str(topic or ""),
            "source_model": index.source_model(),
        }
    max_len = _normalize_max_chars(max_chars)
    body = section.text if section else resolved.text
    body_excerpt = body[:max_len]
    truncated = len(body) > max_len
    payload = resolved.compact_dict(include_sections=True)
    if section:
        payload.update({
            "section_id": section.id,
            "title": section.title,
            "anchor": section.anchor,
            "path_anchor": section.path_anchor,
            "line_start": section.line_start,
            "line_end": section.line_end,
        })
    else:
        payload.update({"anchor": "", "path_anchor": resolved.source_path})
    payload.update({
        "type": "help_topic",
        "schema_version": HELP_SCHEMA_VERSION,
        "status": "ok",
        "body_excerpt": body_excerpt,
        "truncated": truncated,
        "source_model": index.source_model(),
    })
    return payload


def search_help(query: str, *, limit: Any = DEFAULT_SEARCH_LIMIT, base_dir: str | Path = "") -> dict[str, Any]:
    index = build_help_index(base_dir)
    query = str(query or "").strip()
    if not query:
        return {
            "type": "help_search",
            "schema_version": HELP_SCHEMA_VERSION,
            "status": "no_query",
            "query": query,
            "results": [],
            "message": "Provide a search query.",
            "source_model": index.source_model(),
            "index_hash": index.index_hash,
        }
    scored: list[tuple[float, HelpTopic, HelpSection | None]] = []
    for topic in index.topics:
        topic_score = _score_text(query, topic.title, topic.summary + "\n" + topic.text[:2500], topic.source_path)
        if topic_score > 0:
            scored.append((topic_score, topic, None))
        for section in topic.sections:
            section_score = _score_text(query, section.title, section.text, topic.source_path)
            if section_score > 0:
                scored.append((section_score + 1.0, topic, section))
    scored.sort(key=lambda item: (-item[0], item[1].source_path, item[2].line_start if item[2] else 0))
    seen_keys: set[tuple[str, str]] = set()
    results: list[dict[str, Any]] = []
    for score, topic, section in scored:
        key = (topic.source_path, section.anchor if section else "")
        if key in seen_keys:
            continue
        seen_keys.add(key)
        results.append(_result_dict(topic, section, score, query))
        if len(results) >= _normalize_limit(limit):
            break
    status = "ok" if results else "no_answer"
    return {
        "type": "help_search",
        "schema_version": HELP_SCHEMA_VERSION,
        "status": status,
        "query": query,
        "results": results,
        "message": "" if results else "No maintained Torque Help docs matched. Try broader terms or inspect help_list.",
        "source_model": index.source_model(),
        "index_hash": index.index_hash,
    }


def query_help(question: str, *, limit: Any = 5, base_dir: str | Path = "") -> dict[str, Any]:
    question = str(question or "").strip()
    search = search_help(question, limit=limit, base_dir=base_dir)
    results = search.get("results", []) if isinstance(search, dict) else []
    if not results:
        return {
            "type": "help_query",
            "schema_version": HELP_SCHEMA_VERSION,
            "status": "no_answer",
            "question": question,
            "answer": "No maintained Torque Help documentation matched this question. Try `help_search` with broader terms or inspect `help_list`.",
            "results": [],
            "sources": [],
            "source_model": search.get("source_model", {}) if isinstance(search, dict) else {},
            "index_hash": search.get("index_hash", "") if isinstance(search, dict) else "",
        }
    sources = []
    answer_lines = [
        "Deterministic Torque Help lookup found these maintained documentation sections:",
    ]
    for idx, result in enumerate(results[:_normalize_limit(limit, 5)], start=1):
        ref = result.get("path_anchor") or result.get("source_path")
        title = result.get("title") or result.get("topic_title")
        excerpt_text = result.get("excerpt") or result.get("summary") or ""
        answer_lines.append(f"{idx}. {title} — {excerpt_text} Source: {ref}")
        sources.append({
            "title": title,
            "source_path": result.get("source_path", ""),
            "anchor": result.get("anchor", ""),
            "path_anchor": ref,
            "source_hash": result.get("source_hash", ""),
            "restricted_safe": bool(result.get("restricted_safe", False)),
        })
    return {
        "type": "help_query",
        "schema_version": HELP_SCHEMA_VERSION,
        "status": "answered",
        "question": question,
        "answer": "\n".join(answer_lines),
        "results": results,
        "sources": sources,
        "source_model": search.get("source_model", {}),
        "index_hash": search.get("index_hash", ""),
    }


def handle_help_command(data: dict[str, Any] | None, *, base_dir: str | Path = "") -> dict[str, Any]:
    data = dict(data or {})
    cmd = str(data.get("cmd", "") or "").strip()
    if cmd == "help_list":
        return list_help_topics(audience=str(data.get("audience", "") or ""), base_dir=base_dir)
    if cmd == "help_show":
        topic = str(data.get("topic", "") or data.get("path", "") or data.get("id", "") or "")
        return show_help_topic(topic, max_chars=data.get("max_chars", DEFAULT_SHOW_CHARS), base_dir=base_dir)
    if cmd == "help_search":
        query = str(data.get("query", "") or data.get("q", "") or "")
        return search_help(query, limit=data.get("limit", DEFAULT_SEARCH_LIMIT), base_dir=base_dir)
    if cmd == "help_query":
        question = str(data.get("question", "") or data.get("query", "") or data.get("q", "") or "")
        return query_help(question, limit=data.get("limit", 5), base_dir=base_dir)
    return {
        "type": "error",
        "message": f"Unknown Help command: {cmd}",
    }


def help_tool_specs(prefix: str) -> list[dict[str, Any]]:
    prefix = str(prefix or "")
    noun = "Torque Help"
    return [
        {
            "name": f"{prefix}help_list",
            "description": (
                f"List {noun} topics from the maintained markdown allow-list. "
                "Read-only and restricted-agent safe; returns source paths, summaries, hashes, audience tags, and sections."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "audience": {"type": "string", "description": "Optional audience tag filter, e.g. user, agent, worker, engineer, architect, operator."},
                },
            },
        },
        {
            "name": f"{prefix}help_show",
            "description": (
                f"Show one {noun} topic or section by topic id, source path, or path#anchor. "
                "Only maintained Help markdown sources are readable."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic id, source path, or path#anchor."},
                    "path": {"type": "string", "description": "Alias for topic."},
                    "max_chars": {"type": "integer", "description": "Maximum body excerpt characters (default 8000, max 20000)."},
                },
                "required": ["topic"],
            },
        },
        {
            "name": f"{prefix}help_search",
            "description": (
                f"Search {noun} deterministically. Returns ranked excerpts with source paths, anchors, hashes, and safe no-answer fallback."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search text."},
                    "limit": {"type": "integer", "description": "Maximum results (default 8, max 50)."},
                },
                "required": ["query"],
            },
        },
        {
            "name": f"{prefix}help_query",
            "description": (
                f"Answer a question using extractive {noun} lookup only. Returns concise snippets and source references; no hidden state or AI embeddings."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Question to answer from maintained docs."},
                    "query": {"type": "string", "description": "Alias for question."},
                    "limit": {"type": "integer", "description": "Maximum source snippets (default 5, max 50)."},
                },
                "required": ["question"],
            },
        },
    ]


def dispatch_help_tool(name: str, args: dict[str, Any] | None, *, prefix: str = "") -> tuple[str, bool]:
    tool_name = str(name or "").strip()
    prefix = str(prefix or "")
    suffix = tool_name[len(prefix):] if prefix and tool_name.startswith(prefix) else tool_name
    suffix = suffix.removeprefix("help_")
    args = dict(args or {})
    if suffix == "list":
        payload = handle_help_command({"cmd": "help_list", **args})
    elif suffix == "show":
        payload = handle_help_command({"cmd": "help_show", **args})
    elif suffix == "search":
        payload = handle_help_command({"cmd": "help_search", **args})
    elif suffix == "query":
        payload = handle_help_command({"cmd": "help_query", **args})
    else:
        payload = {"type": "error", "message": f"Unknown Help tool: {tool_name}"}
    return json.dumps(payload, separators=(",", ":")), payload.get("type") == "error"
