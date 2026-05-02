from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass


ARXIV_API = "http://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass(frozen=True)
class Paper:
    arxiv_id: str
    title: str
    abstract: str
    authors: list[str]
    categories: list[str]
    url: str


def fetch_papers(queries: list[str], max_per_query: int = 3) -> list[Paper]:
    seen: set[str] = set()
    papers: list[Paper] = []
    for i, query in enumerate(queries):
        if i > 0:
            time.sleep(3)
        try:
            params = urllib.parse.urlencode({
                "search_query": f"all:{query}",
                "max_results": max_per_query,
                "sortBy": "relevance",
            })
            url = f"{ARXIV_API}?{params}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml_bytes = resp.read()
            for paper in _parse_atom_response(xml_bytes):
                if paper.arxiv_id not in seen:
                    seen.add(paper.arxiv_id)
                    papers.append(paper)
        except Exception:
            continue
    return papers


def format_paper_context(papers: list[Paper], max_chars: int = 2000) -> str:
    if not papers:
        return ""
    parts: list[str] = []
    total = 0
    for p in papers:
        abstract = p.abstract[:200].rsplit(" ", 1)[0] + "..."
        entry = f"- {p.title} [{p.arxiv_id}]: {abstract}"
        if total + len(entry) > max_chars:
            break
        parts.append(entry)
        total += len(entry)
    return "\n".join(parts)


def _parse_atom_response(xml_bytes: bytes) -> list[Paper]:
    root = ET.fromstring(xml_bytes)
    papers: list[Paper] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        raw_id = (entry.findtext("atom:id", "", ATOM_NS) or "").strip()
        arxiv_id = raw_id.rsplit("/", 1)[-1] if raw_id else ""
        if not arxiv_id:
            continue
        title = " ".join((entry.findtext("atom:title", "", ATOM_NS) or "").split())
        abstract = " ".join((entry.findtext("atom:summary", "", ATOM_NS) or "").split())
        authors = [
            (a.findtext("atom:name", "", ATOM_NS) or "").strip()
            for a in entry.findall("atom:author", ATOM_NS)
        ]
        categories = [
            c.get("term", "")
            for c in entry.findall("{http://www.w3.org/2005/Atom}category")
        ]
        papers.append(Paper(
            arxiv_id=arxiv_id,
            title=title,
            abstract=abstract,
            authors=[a for a in authors if a],
            categories=[c for c in categories if c],
            url=raw_id,
        ))
    return papers
