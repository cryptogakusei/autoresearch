from __future__ import annotations

from unittest.mock import patch

from autoresearch.arxiv import Paper, _parse_atom_response, fetch_papers, format_paper_context

SAMPLE_ATOM = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2302.14231v1</id>
    <title>Fourier Neural Operator for Parametric PDEs</title>
    <summary>We propose a neural operator that learns in Fourier space.</summary>
    <author><name>Zongyi Li</name></author>
    <author><name>Nikola Kovachki</name></author>
    <category term="cs.LG"/>
    <category term="math.NA"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2301.99999v1</id>
    <title>Spectral Methods for Neural PDEs</title>
    <summary>A short abstract.</summary>
    <author><name>Jane Doe</name></author>
    <category term="cs.LG"/>
  </entry>
</feed>
"""


def test_parse_atom_response():
    papers = _parse_atom_response(SAMPLE_ATOM)
    assert len(papers) == 2
    assert papers[0].arxiv_id == "2302.14231v1"
    assert papers[0].title == "Fourier Neural Operator for Parametric PDEs"
    assert "Fourier space" in papers[0].abstract
    assert papers[0].authors == ["Zongyi Li", "Nikola Kovachki"]
    assert "cs.LG" in papers[0].categories
    assert papers[1].arxiv_id == "2301.99999v1"


def test_deduplication():
    double_atom = SAMPLE_ATOM + b"""\
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2302.14231v1</id>
    <title>Duplicate</title>
    <summary>Dup</summary>
    <author><name>X</name></author>
  </entry>
</feed>
"""

    def mock_urlopen(req, timeout=10):
        class Resp:
            def read(self):
                return SAMPLE_ATOM

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        return Resp()

    with patch("autoresearch.arxiv.urllib.request.urlopen", side_effect=mock_urlopen):
        with patch("autoresearch.arxiv.time.sleep"):
            papers = fetch_papers(["query1", "query2"], max_per_query=5)
    ids = [p.arxiv_id for p in papers]
    assert ids.count("2302.14231v1") == 1
    assert ids.count("2301.99999v1") == 1


def test_format_paper_context_empty():
    assert format_paper_context([]) == ""


def test_format_paper_context_truncation():
    papers = [
        Paper(
            arxiv_id=f"id{i}",
            title=f"Title {i}",
            abstract="word " * 100,
            authors=[],
            categories=[],
            url=f"http://arxiv.org/abs/id{i}",
        )
        for i in range(20)
    ]
    result = format_paper_context(papers, max_chars=500)
    assert len(result) <= 600


def test_fetch_papers_network_failure():
    with patch(
        "autoresearch.arxiv.urllib.request.urlopen",
        side_effect=Exception("network down"),
    ):
        with patch("autoresearch.arxiv.time.sleep"):
            papers = fetch_papers(["test query"])
    assert papers == []
