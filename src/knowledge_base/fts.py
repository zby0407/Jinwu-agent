"""FTS5 query/index text preparation for the knowledge base.

SQLite's default ``unicode61`` tokenizer treats a run of CJK characters as a
single token, so a query like "极区前兆" never matches indexed text like
"极区磁场前兆". We therefore pre-tokenize both indexed text and queries:
ASCII/alphanumeric runs pass through as words, CJK runs are emitted as
overlapping bigrams (single characters stand alone). Queries become an OR of
quoted tokens so any shared bigram/word produces a hit, ranked by bm25.
"""

from __future__ import annotations

import re

_CJK_RUN = re.compile(r"[㐀-鿿豈-﫿]+")
_WORD_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_TOKEN_RUN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.-]*|[㐀-鿿豈-﫿]+"
)


def tokenize(text: str) -> str:
    """Convert raw text into space-separated index tokens (CJK bigrams)."""

    tokens: list[str] = []
    for match in _TOKEN_RUN.finditer(text or ""):
        chunk = match.group(0)
        if _CJK_RUN.fullmatch(chunk):
            if len(chunk) == 1:
                tokens.append(chunk)
            else:
                tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
        else:
            tokens.append(chunk)
    return " ".join(tokens)


def query_to_match(query: str) -> str:
    """Build an FTS5 MATCH expression: OR of quoted tokens from ``query``."""

    tokens = tokenize(query).split()
    if not tokens:
        return ""
    seen: set[str] = set()
    parts: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        parts.append('"' + token.replace('"', '""') + '"')
    return " OR ".join(parts)
