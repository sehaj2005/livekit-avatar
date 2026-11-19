"""Local medicine-reference storage and hybrid lexical/vector retrieval.

A deliberately offline MVP: TF-IDF term vectors, not clinical reasoning or
semantic neural embeddings. Only administrator-reviewed reference material
should be ingested. Documents are treated as untrusted reference text.
"""
from __future__ import annotations

import math
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("MEDICINE_RAG_DB", str(BASE_DIR / "medicine_data" / "medicine_rag.sqlite3")))
WORD_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9-]{1,}")
MAX_RESULTS = 8


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def initialize() -> None:
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS medicine_sources (
                source_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                reviewed_by TEXT NOT NULL,
                reviewed_on TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS medicine_chunks (
                chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL REFERENCES medicine_sources(source_id),
                page_number INTEGER,
                chunk_index INTEGER NOT NULL,
                body TEXT NOT NULL,
                UNIQUE(source_id, page_number, chunk_index)
            );
            CREATE INDEX IF NOT EXISTS idx_medicine_chunks_source
            ON medicine_chunks(source_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS medicine_chunks_fts USING fts5(
                body, content='medicine_chunks', content_rowid='chunk_id'
            );
            CREATE TRIGGER IF NOT EXISTS medicine_chunks_ai AFTER INSERT ON medicine_chunks BEGIN
                INSERT INTO medicine_chunks_fts(rowid, body) VALUES (new.chunk_id, new.body);
            END;
            CREATE TRIGGER IF NOT EXISTS medicine_chunks_ad AFTER DELETE ON medicine_chunks BEGIN
                INSERT INTO medicine_chunks_fts(medicine_chunks_fts, rowid, body)
                VALUES ('delete', old.chunk_id, old.body);
            END;
            CREATE TRIGGER IF NOT EXISTS medicine_chunks_au AFTER UPDATE ON medicine_chunks BEGIN
                INSERT INTO medicine_chunks_fts(medicine_chunks_fts, rowid, body)
                VALUES ('delete', old.chunk_id, old.body);
                INSERT INTO medicine_chunks_fts(rowid, body) VALUES (new.chunk_id, new.body);
            END;
        """)


def tokenize(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())[:2500]


def chunk_text(text: str, size: int = 950, overlap: int = 140) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        stop = min(start + size, len(text))
        if stop < len(text):
            split = text.rfind(" ", start + size // 2, stop)
            if split > start:
                stop = split
        part = text[start:stop].strip()
        if part:
            chunks.append(part)
        if stop == len(text):
            break
        start = max(start + 1, stop - overlap)
    return chunks


def upsert_source(
    source_id: str, filename: str, title: str, source_url: str,
    reviewed_by: str, reviewed_on: str, pages: list[tuple[int | None, str]],
) -> int:
    initialize()
    pending: list[tuple[str, int | None, int, str]] = []
    for page, body in pages:
        for index, chunk in enumerate(chunk_text(body)):
            pending.append((source_id, page, index, chunk))
    if not pending:
        raise ValueError("Document has no extractable text. Scanned PDFs require a separate OCR/review step.")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO medicine_sources (source_id,filename,title,source_url,reviewed_by,reviewed_on) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET "
            "filename=excluded.filename,title=excluded.title,source_url=excluded.source_url, "
            "reviewed_by=excluded.reviewed_by,reviewed_on=excluded.reviewed_on,updated_at=CURRENT_TIMESTAMP",
            (source_id, filename, title, source_url, reviewed_by, reviewed_on),
        )
        db.execute("DELETE FROM medicine_chunks WHERE source_id=?", (source_id,))
        db.executemany(
            "INSERT INTO medicine_chunks(source_id,page_number,chunk_index,body) VALUES (?,?,?,?)",
            pending,
        )
    return len(pending)


def search(query: str, top_k: int = 5) -> list[dict]:
    initialize()
    top_k = max(1, min(MAX_RESULTS, top_k))
    terms = list(dict.fromkeys(tokenize(query)))[:20]
    if not terms:
        return []

    with connect() as db:
        # Limit the size of this MVP index. Use Qdrant/FAISS for a large corpus.
        rows = db.execute("""
            SELECT c.chunk_id, c.body, c.page_number,
                   s.source_id, s.filename, s.title, s.source_url,
                   s.reviewed_by, s.reviewed_on
            FROM medicine_chunks c JOIN medicine_sources s ON s.source_id=c.source_id
            ORDER BY c.chunk_id LIMIT 2500
        """).fetchall()
        if not rows:
            return []
        # FTS5 MATCH expression is built exclusively from sanitized WORD_RE tokens.
        expression = " OR ".join('"' + term.replace('"', '') + '"' for term in terms)
        try:
            hits = db.execute(
                "SELECT rowid, bm25(medicine_chunks_fts) AS rank FROM medicine_chunks_fts "
                "WHERE medicine_chunks_fts MATCH ? ORDER BY rank LIMIT 100",
                (expression,),
            ).fetchall()
        except sqlite3.OperationalError:
            hits = []
    fts_ids = {int(row["rowid"]) for row in hits}
    doc_tokens = [Counter(tokenize(row["body"])) for row in rows]
    document_frequency: Counter = Counter()
    for tokens in doc_tokens:
        document_frequency.update(tokens.keys())
    n = len(rows)
    idf = {term: math.log((1 + n) / (1 + df)) + 1 for term, df in document_frequency.items()}

    def vector(counts: Counter) -> dict[str, float]:
        return {term: (1 + math.log(freq)) * idf.get(term, 1.0) for term, freq in counts.items() if freq}

    q_vec = vector(Counter(terms))
    q_norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1
    scored = []
    for row, counts in zip(rows, doc_tokens):
        vec = vector(counts)
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1
        cosine = sum(weight * vec.get(term, 0) for term, weight in q_vec.items()) / (q_norm * norm)
        exact_hits = sum(1 for term in terms if term in counts)
        if cosine <= 0 and not exact_hits and int(row["chunk_id"]) not in fts_ids:
            continue
        score = cosine + (0.08 if int(row["chunk_id"]) in fts_ids else 0) + 0.01 * exact_hits
        scored.append((score, row))

    scored.sort(key=lambda item: item[0], reverse=True)
    result = []
    per_source = Counter()
    for score, row in scored:
        # Retrieval is a search aid, not an estimate of clinical applicability.
        if score < 0.12:
            continue
        if per_source[row["source_id"]] >= 3:
            continue
        per_source[row["source_id"]] += 1
        result.append({
            "citation_id": f"{row['source_id']}:p{row['page_number'] or 0}:c{row['chunk_id']}",
            "source_id": row["source_id"],
            "title": row["title"],
            "filename": row["filename"],
            "source_url": row["source_url"],
            "page": row["page_number"],
            "reviewed_by": row["reviewed_by"],
            "reviewed_on": row["reviewed_on"],
            "excerpt": row["body"][:1100],
            "retrieval_score": round(float(score), 4),
        })
        if len(result) >= top_k:
            break
    return result
