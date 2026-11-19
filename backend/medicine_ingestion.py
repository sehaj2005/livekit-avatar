
"""
Mateo AI - Medicine RAG Document Ingestion

Supported file formats:
    PDF
    TXT
    MD
    JSON

Features:
    - Extract medicine reference documents
    - Support structured medicine JSON catalogs
    - Preserve source information
    - Extract medicine ingredients and precautions
    - Prevent draft medicines from being indexed as approved
    - Store reviewed documents in the local RAG database

Usage:
    python -m backend.medicine_ingestion <file_path> \
        --title "Document Title" \
        --source-url "Publication URL" \
        --reviewed-by "Actual Reviewer" \
        --reviewed-on "YYYY-MM-DD"
"""

from __future__ import annotations

import argparse
import hashlib
import json

from datetime import date
from pathlib import Path
from typing import Any

from backend.medicine_store import upsert_source


MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_PDF_PAGES = 500

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".md",
    ".json",
}


MEDICINE_FIELDS = (
    "id",
    "ingredient",
    "category",
    "used_for",
    "cautions",
    "clinical_review_required",
    "status",
    "sources",
    "medicine_name",
    "active_ingredients",
    "indications",
    "contraindications",
    "precautions",
    "adverse_effects",
    "interactions",
    "prescription_status",
    "source_note",
)


def format_field_value(value: Any) -> str:
    """
    Convert JSON values into searchable text.
    """

    if value is None:
        return ""

    if isinstance(value, bool):
        return str(value)

    if isinstance(value, list):
        return "; ".join(
            format_field_value(item)
            for item in value
            if item is not None
        )

    if isinstance(value, dict):
        return json.dumps(
            value,
            ensure_ascii=False,
        )

    return str(value)


def extract_json_records(
    path: Path,
) -> list[tuple[int | None, str]]:
    """
    Extract medicine information from JSON.

    Supports:

    1. A JSON list of medicine records.

    2. A JSON object containing an 'items' list.
    """

    data = json.loads(
        path.read_text(encoding="utf-8")
    )

    metadata = {}

    if isinstance(data, dict):
        records = data.get("items")

        metadata = {
            key: data.get(key)
            for key in (
                "document_status",
                "reviewer",
                "prepared_on",
                "purpose",
            )
        }

    elif isinstance(data, list):
        records = data

    else:
        raise ValueError(
            "JSON root must be a list or an object containing 'items'."
        )

    if not isinstance(records, list):
        raise ValueError(
            "JSON must contain a list of medicine records."
        )

    text_rows = []

    for index, record in enumerate(
        records,
        start=1,
    ):

        if not isinstance(record, dict):
            raise ValueError(
                f"Medicine record {index} must be a JSON object."
            )

        lines = []

        for field in MEDICINE_FIELDS:

            if field not in record:
                continue

            value = format_field_value(
                record[field]
            )

            if not value.strip():
                continue

            display_name = (
                field.replace("_", " ").title()
            )

            lines.append(
                f"{display_name}: {value}"
            )

        if lines:
            text_rows.append(
                "\n".join(lines)
            )

    metadata_lines = []

    for key, value in metadata.items():

        if value is None:
            continue

        metadata_lines.append(
            f"{key.replace('_', ' ').title()}: "
            f"{format_field_value(value)}"
        )

    sections = []

    if metadata_lines:
        sections.append(
            "\n".join(metadata_lines)
        )

    sections.extend(text_rows)

    extracted_text = "\n\n".join(
        sections
    )

    return [
        (None, extracted_text)
    ]


def extract_pages(
    path: Path,
) -> list[tuple[int | None, str]]:
    """
    Extract text from supported documents.

    Returns:
        List of (page_number, text)
    """

    extension = path.suffix.lower()

    if extension == ".pdf":

        import pymupdf

        with pymupdf.open(path) as document:

            if document.page_count > MAX_PDF_PAGES:
                raise ValueError(
                    "PDF exceeds the 500-page import limit."
                )

            pages = []

            for index, page in enumerate(
                document
            ):

                text = page.get_text(
                    "text",
                    sort=True,
                )

                pages.append(
                    (index + 1, text)
                )

            return pages

    if extension in (".txt", ".md"):

        text = path.read_text(
            encoding="utf-8"
        )

        return [
            (None, text)
        ]

    if extension == ".json":

        return extract_json_records(
            path
        )

    raise ValueError(
        "Unsupported document format. "
        "Supported: PDF, TXT, MD, JSON."
    )


def is_unreviewed_json(
    path: Path,
) -> bool:
    """
    Prevent explicitly unreviewed JSON records
    from being stored as clinically approved.
    """

    if path.suffix.lower() != ".json":
        return False

    data = json.loads(
        path.read_text(encoding="utf-8")
    )

    if isinstance(data, dict):

        document_status = str(
            data.get("document_status", "")
        ).lower()

        if any(
            word in document_status
            for word in (
                "draft",
                "unreviewed",
                "pending",
            )
        ):
            return True

        records = data.get("items", [])

    elif isinstance(data, list):

        records = data

    else:
        return True

    if not isinstance(records, list):
        return True

    for record in records:

        if not isinstance(record, dict):
            return True

        status = str(
            record.get("status", "")
        ).lower()

        if any(
            word in status
            for word in (
                "draft",
                "unreviewed",
                "pending",
            )
        ):
            return True

        if record.get(
            "clinical_review_required"
        ) is True:
            return True

    return False


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Import clinically reviewed medicine "
            "reference material into Mateo RAG."
        )
    )

    parser.add_argument(
        "path",
        type=Path,
        help="Path to medicine document.",
    )

    parser.add_argument(
        "--title",
        required=True,
        help="Document title.",
    )

    parser.add_argument(
        "--source-url",
        required=True,
        help="Original publication URL or reference.",
    )

    parser.add_argument(
        "--reviewed-by",
        required=True,
        help="Actual clinical reviewer.",
    )

    parser.add_argument(
        "--reviewed-on",
        required=True,
        help="Clinical review date (YYYY-MM-DD).",
    )

    args = parser.parse_args()

    reviewed_on = date.fromisoformat(
        args.reviewed_on
    )

    if reviewed_on > date.today():
        raise ValueError(
            "Clinical review date cannot be in the future."
        )

    file_path = args.path.resolve(
        strict=True
    )

    if not file_path.is_file():
        raise ValueError(
            "The provided path is not a file."
        )

    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            "Unsupported file format."
        )

    if file_path.stat().st_size > MAX_FILE_SIZE:
        raise ValueError(
            "File exceeds the 20 MB limit."
        )

    if not args.title.strip():
        raise ValueError(
            "Document title is required."
        )

    if not args.source_url.strip():
        raise ValueError(
            "Source reference is required."
        )

    if not args.reviewed_by.strip():
        raise ValueError(
            "Clinical reviewer is required."
        )

    if is_unreviewed_json(file_path):
        raise ValueError(
            "This JSON catalog contains draft or "
            "unreviewed medicine records. "
            "It cannot be imported into the "
            "clinically reviewed RAG index."
        )

    pages = extract_pages(
        file_path
    )

    total_characters = sum(
        len(text)
        for _, text in pages
    )

    if total_characters == 0:
        raise ValueError(
            "No searchable content found in document."
        )

    source_id = hashlib.sha256(
        str(file_path).encode("utf-8")
    ).hexdigest()[:20]

    chunk_count = upsert_source(
        source_id=source_id,
        filename=file_path.name,
        title=args.title.strip(),
        source_url=args.source_url.strip(),
        reviewed_by=args.reviewed_by.strip(),
        reviewed_on=args.reviewed_on,
        pages=pages,
    )

    print()
    print("Mateo Medicine RAG Ingestion")
    print("----------------------------")
    print(f"Document: {file_path.name}")
    print(f"Characters: {total_characters}")
    print(f"Indexed chunks: {chunk_count}")
    print(f"Source ID: {source_id}")
    print()
    print("No clinical recommendations were generated.")


if __name__ == "__main__":
    main()
