"""Offline TF-IDF retriever over the KB few-shot examples (stdlib only).

This is the "RAG" half of the v0.2.0 AI core: instead of injecting the
first-N few-shot examples into the extractor prompt regardless of content,
the pipeline ranks examples by TF-IDF cosine similarity between the
incoming document text (query) and each example's ``input_text`` and keeps
the top-N. No embeddings, no vector DB, no network — the whole index is a
small JSON file built by ``api/scripts/ingest_kb.py``.

Public helpers:
    tokenize(text) -> list[str]
    example_text(example) -> str
    rank_examples(examples, query, limit) -> list[dict]  (pure in-memory)
    build_index(grouped_examples) -> dict               (for ingest script)
    score_against_index(index_entry, query_tokens, doc_freq, n_docs) -> float
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens. Short tokens (<2 chars) are dropped."""
    return [tok for tok in _TOKEN_RE.findall((text or "").lower()) if len(tok) > 1]


def example_text(example: dict[str, Any]) -> str:
    """Retrievable text for one few-shot example.

    Uses ``input_text`` (the OCR-like content) plus ``description`` when
    present. The ``output`` mapping is deliberately excluded: retrieval must
    match on what the document *looks like*, not on answer keys.
    """
    parts = [str(example.get("description", "")), str(example.get("input_text", ""))]
    return "\n".join(p for p in parts if p).strip()


def _tfidf_cosine(query_counts: Counter, doc_counts: Counter, doc_freq: Counter, n_docs: int) -> float:
    """Cosine similarity between TF-IDF weighted query and document vectors."""
    if not query_counts or not doc_counts or n_docs <= 0:
        return 0.0
    query_len = sum(query_counts.values())
    doc_len = sum(doc_counts.values())
    if query_len == 0 or doc_len == 0:
        return 0.0

    def idf(term: str) -> float:
        return math.log((1 + n_docs) / (1 + doc_freq.get(term, 0))) + 1.0

    dot = 0.0
    query_norm = 0.0
    for term, count in query_counts.items():
        weight = (count / query_len) * idf(term)
        query_norm += weight * weight
        if term in doc_counts:
            dot += weight * (doc_counts[term] / doc_len) * idf(term)
    doc_norm = sum(((c / doc_len) * idf(t)) ** 2 for t, c in doc_counts.items())
    if query_norm <= 0.0 or doc_norm <= 0.0:
        return 0.0
    return dot / (math.sqrt(query_norm) * math.sqrt(doc_norm))


def rank_examples(
    examples: list[dict[str, Any]],
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Rank few-shot examples by TF-IDF cosine similarity to *query*.

    Ties (including an empty/whitespace query, where every score is 0.0)
    keep the original sorted-file order, so behaviour degrades gracefully to
    the legacy first-N selection. Always returns at most *limit* items.
    """
    if limit <= 0:
        return []
    if not query or not query.strip():
        return list(examples[:limit])

    tokenized = [Counter(tokenize(example_text(ex))) for ex in examples]
    doc_freq: Counter = Counter()
    for counts in tokenized:
        doc_freq.update(counts.keys())
    query_counts = Counter(tokenize(query))

    scored = [
        (_tfidf_cosine(query_counts, counts, doc_freq, len(examples)), idx)
        for idx, counts in enumerate(tokenized)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [examples[idx] for _, idx in scored[:limit]]


def build_index(grouped_examples: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Build a serializable TF-IDF index over ``{doc_type: [examples]}``.

    The index stores per-example token counts plus corpus document
    frequencies, so ``rank_examples``-equivalent scoring can run without
    re-reading the KB. Written to ``rag_index.json`` by ``ingest_kb.py``.
    """
    index: dict[str, Any] = {"doc_types": {}}
    for doc_type, examples in grouped_examples.items():
        tokenized = [Counter(tokenize(example_text(ex))) for ex in examples]
        doc_freq: Counter = Counter()
        for counts in tokenized:
            doc_freq.update(counts.keys())
        index["doc_types"][doc_type] = {
            "n_docs": len(examples),
            "doc_freq": dict(doc_freq),
            "docs": [
                {"tokens": dict(counts), "length": sum(counts.values())}
                for counts in tokenized
            ],
        }
    return index


def score_against_index(
    doc_entry: dict[str, Any],
    query: str,
    doc_freq: dict[str, int],
    n_docs: int,
) -> float:
    """Score one indexed doc against *query* (same weighting as rank)."""
    query_counts = Counter(tokenize(query))
    doc_counts = Counter({t: int(c) for t, c in doc_entry.get("tokens", {}).items()})
    return _tfidf_cosine(query_counts, doc_counts, Counter(doc_freq), n_docs)
