"""The arXiv adapter must send a boolean AND the API understands.

A previous build joined terms with ``+AND+`` and then percent-encoded the
string, turning the separator into ``%2BAND%2B``.  arXiv returned an empty
feed for every multi-word query, so this test pins the wire format.
"""

from __future__ import annotations

from knowledge_base import literature

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1234.56789v1</id>
    <published>2020-01-02T00:00:00Z</published>
    <title>A supernova light curve</title>
    <summary>We present photometry.</summary>
    <author><name>Jane Astro</name></author>
    <link title="pdf" href="http://arxiv.org/pdf/1234.56789v1"/>
  </entry>
</feed>
"""


def test_multi_word_arxiv_query_keeps_boolean_separators(monkeypatch):
    seen: list[str] = []

    def fake_get(url: str, **_kwargs) -> str:
        seen.append(url)
        return ATOM

    monkeypatch.setattr(literature, "_http_get", fake_get)
    items = literature._search_arxiv("supernova light curve", 5, None, None, "relevance")

    assert len(items) == 1
    assert items[0]["title"] == "A supernova light curve"
    assert len(seen) == 1
    url = seen[0]
    assert "search_query=all%3Asupernova+AND+all%3Alight+AND+all%3Acurve" in url
    assert "%2BAND%2B" not in url
