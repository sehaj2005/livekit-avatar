
import argparse
import re
import sqlite3
from pathlib import Path

from backend.medicine_ingestion import extract_pages


DATA_DIR = Path("medicine_data")
DB_PATH = DATA_DIR / "medicine_dev.sqlite3"

SUPPORTED = {".pdf", ".txt", ".md", ".json"}

EXCLUDED = {
    "README.md",
    "medicine_catalog_items.json",
}


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def create_tables(db):
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS medicine_chunks
        USING fts5(
            filename UNINDEXED,
            page_number UNINDEXED,
            content,
            tokenize='unicode61'
        )
    """)


def split_text(text, size=900):
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return []

    chunks = []
    position = 0

    while position < len(text):
        end = min(position + size, len(text))

        chunks.append(text[position:end])
        position = end

    return chunks


def build_index():
    if not DATA_DIR.exists():
        raise FileNotFoundError(
            "medicine_data folder does not exist."
        )

    with connect() as db:
        create_tables(db)

        db.execute("DELETE FROM medicine_chunks")

        total = 0

        for file in sorted(DATA_DIR.iterdir()):
            if not file.is_file():
                continue

            if file.name in EXCLUDED:
                continue

            if file.suffix.lower() not in SUPPORTED:
                continue

            try:
                pages = extract_pages(file)

                count = 0

                for page_number, text in pages:
                    for chunk in split_text(text):
                        db.execute(
                            """
                            INSERT INTO medicine_chunks
                            (filename, page_number, content)
                            VALUES (?, ?, ?)
                            """,
                            (
                                file.name,
                                page_number,
                                chunk,
                            ),
                        )

                        count += 1

                total += count

                print(
                    f"Indexed {file.name}: {count} chunks"
                )

            except Exception as exc:
                print(
                    f"Skipped {file.name}: {exc}"
                )

        db.commit()

    print()
    print(f"Total indexed chunks: {total}")
    print("Index status: DRAFT_UNREVIEWED")
    print("No medicine recommendations generated.")


def search_documents(query, limit=5):
    terms = re.findall(r"\w+", query.lower())

    if not terms:
        return []

    fts_query = " OR ".join(
        f'"{term}"' for term in terms
    )

    with connect() as db:
        create_tables(db)

        rows = db.execute(
            """
            SELECT
                filename,
                page_number,
                snippet(
                    medicine_chunks,
                    2,
                    '[[',
                    ']]',
                    '...',
                    45
                ) AS excerpt,
                bm25(medicine_chunks) AS score
            FROM medicine_chunks
            WHERE medicine_chunks MATCH ?
            ORDER BY score ASC
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()

    return rows


def main():
    parser = argparse.ArgumentParser(
        description="Mateo development-only medicine search"
    )

    parser.add_argument(
        "action",
        choices=["index", "search"],
    )

    parser.add_argument(
        "query",
        nargs="?",
        default="",
    )

    args = parser.parse_args()

    if args.action == "index":
        build_index()
        return

    if not args.query.strip():
        parser.error(
            "Provide a search query."
        )

    results = search_documents(args.query)

    print()
    print("Mateo Medicine RAG - Development Search")
    print("Status: DRAFT_UNREVIEWED")
    print()

    if not results:
        print("No matching documents found.")
        return

    for index, row in enumerate(results, start=1):
        filename, page_number, excerpt, score = row

        print(f"Result {index}")
        print(f"Source: {filename}")

        if page_number is not None:
            print(f"Page: {page_number}")

        print(f"Score: {score:.4f}")
        print(f"Excerpt: {excerpt}")
        print("-" * 50)


if __name__ == "__main__":
    main()
