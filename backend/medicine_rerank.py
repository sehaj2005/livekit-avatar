
"""
Mateo AI - Medicine RAG Ranking and Deduplication

Development-only retrieval improvements:
- BM25 search
- Query-feature coverage
- One result per document
- Similarity-based duplicate removal
- TXT/MD/PDF priority over catalog JSON

No clinical approval is inferred.
No medications are recommended.
"""

import logging
import re
from pathlib import Path

from backend.medicine_dev_index import (
    DB_PATH,
    search_documents,
)

logger = logging.getLogger("mateo-medicine-rerank")

SIMILARITY_THRESHOLD = 0.80
CANDIDATE_LIMIT = 30


def clean_text(value: str) -> str:
    """Remove FTS highlighting and normalize spacing."""
    value = re.sub(r"\[\[|\]\]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def tokenize(value: str) -> set[str]:
    """Build tokens for duplicate detection."""
    return set(
        re.findall(r"[a-z0-9]+", value.lower())
    )


def similarity(left: str, right: str) -> float:
    """Jaccard word similarity between two excerpts."""
    a = tokenize(left)
    b = tokenize(right)

    if not a or not b:
        return 0.0

    return len(a & b) / len(a | b)


def feature_coverage(
    excerpt: str,
    features: list[str],
) -> int:
    """Count distinct query features mentioned."""
    text = clean_text(excerpt).lower()

    return sum(
        1
        for feature in set(features)
        if re.search(
            rf"\b{re.escape(feature.lower())}\b",
            text,
        )
    )


def normalize_match(match):
    """
    Existing search_documents returns:
    (filename, page_number, excerpt, bm25_score)
    """
    if not isinstance(match, (tuple, list)):
        return None

    if len(match) < 4:
        return None

    filename, page, excerpt, score = match[:4]

    if not filename or not excerpt:
        return None

    try:
        score = float(score)
    except (TypeError, ValueError):
        return None

    source = Path(str(filename)).name

    return {
        "source": source,
        "page": page,
        "excerpt": clean_text(str(excerpt)),
        "bm25_score": score,
        "review_status": "draft_unreviewed",
    }


def retrieve_ranked_references(
    features: list[str],
    limit: int = 5,
) -> dict:
    """
    Search, rerank, and deduplicate reference excerpts.

    Scores here represent search relevance only,
    not clinical quality, correctness, or safety.
    """

    features = [
        value.strip().lower()
        for value in features
        if isinstance(value, str) and value.strip()
    ]

    features = list(dict.fromkeys(features))

    query = " ".join(features)

    output = {
        "status": "draft_unreviewed",
        "retrieval_query": query,
        "total_results": 0,
        "results": [],
    }

    if not features:
        return output

    if not Path(DB_PATH).is_file():
        raise RuntimeError(
            "Medicine index missing. Run: "
            "python -m backend.medicine_dev_index index"
        )

    # Retrieve more candidates than we want to display.
    matches = search_documents(
        query,
        limit=max(CANDIDATE_LIMIT, limit * 5),
    )

    candidates = []

    for match in matches:
        item = normalize_match(match)

        if item is None:
            continue

        coverage = feature_coverage(
            item["excerpt"],
            features,
        )

        if coverage == 0:
            continue

        item["_coverage"] = coverage
        item["_catalog"] = (
            item["source"].lower().endswith(".json")
        )

        candidates.append(item)

    # Catalog data may repeat information already
    # contained in the original reference documents.
    # Prefer standalone reference documents when found.
    text_candidates = [
        item for item in candidates
        if not item["_catalog"]
    ]

    if text_candidates:
        candidates = text_candidates

    # First favor how many query features are covered.
    # Then use the SQLite FTS5 BM25 score.
    # Lower BM25 values are better for this index.
    candidates.sort(
        key=lambda item: (
            -item["_coverage"],
            item["bm25_score"],
            item["source"].lower(),
        )
    )

    selected = []
    used_sources = set()

    for item in candidates:
        source_key = item["source"].casefold()

        # Never return multiple excerpts from
        # the same reference document.
        if source_key in used_sources:
            continue

        # Avoid near-identical text even when
        # copied into separate files.
        is_duplicate = any(
            similarity(
                item["excerpt"],
                existing["excerpt"],
            ) >= SIMILARITY_THRESHOLD
            for existing in selected
        )

        if is_duplicate:
            continue

        selected.append(
            {
                "source": item["source"],
                "page": item["page"],
                "excerpt": item["excerpt"],
                "bm25_score": item["bm25_score"],
                "review_status": "draft_unreviewed",
            }
        )

        used_sources.add(source_key)

        if len(selected) >= limit:
            break

    output["results"] = selected
    output["total_results"] = len(selected)

    logger.info(
        "Medicine RAG reranking: %s candidates, "
        "%s unique references.",
        len(candidates),
        len(selected),
    )

    return output
