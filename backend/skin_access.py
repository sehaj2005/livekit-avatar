"""Persistent analysis quotas shared by backend workers using the same SQLite DB."""
import os
import time
import uuid

from fastapi import HTTPException
from backend import analytics


def reserve_analysis(meeting_id: str, identity: str) -> str:
    now = time.time()
    participant_limit = max(1, int(os.getenv("SKIN_PARTICIPANT_LIMIT_10M", "3")))
    meeting_limit = max(1, int(os.getenv("SKIN_MEETING_LIMIT_1H", "20")))
    concurrent_limit = max(1, int(os.getenv("SKIN_CONCURRENT_LIMIT", "1")))
    lease_seconds = max(900, int(os.getenv("MATEO_OLLAMA_TIMEOUT", "270")) + 240)
    with analytics.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS skin_analysis_requests (
            request_id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL,
            participant_id TEXT NOT NULL, started_at REAL NOT NULL,
            finished_at REAL
        )""")
        db.execute("BEGIN IMMEDIATE")
        registered = db.execute(
            "SELECT 1 FROM participants WHERE meeting_id = ? AND participant_id = ?",
            (meeting_id, identity),
        ).fetchone()
        if not registered:
            raise HTTPException(403, "Participant is not registered in this meeting.")
        participant_count = db.execute(
            "SELECT COUNT(*) FROM skin_analysis_requests WHERE participant_id = ? AND started_at > ?",
            (identity, now - 600),
        ).fetchone()[0]
        meeting_count = db.execute(
            "SELECT COUNT(*) FROM skin_analysis_requests WHERE meeting_id = ? AND started_at > ?",
            (meeting_id, now - 3600),
        ).fetchone()[0]
        if participant_count >= participant_limit or meeting_count >= meeting_limit:
            raise HTTPException(429, "Skin Check usage limit reached. Please try again later.",
                                headers={"Retry-After": "600"})
        # Expire abandoned reservations after both configured model timeouts.
        active = db.execute(
            "SELECT COUNT(*) FROM skin_analysis_requests WHERE finished_at IS NULL AND started_at > ?",
            (now - lease_seconds,),
        ).fetchone()[0]
        if active >= concurrent_limit:
            raise HTTPException(429, "Skin Check is busy. Please try again shortly.",
                                headers={"Retry-After": "15"})
        db.execute("DELETE FROM skin_analysis_requests WHERE started_at < ?", (now - 86400,))
        request_id = uuid.uuid4().hex
        db.execute("INSERT INTO skin_analysis_requests VALUES (?, ?, ?, ?, NULL)",
                   (request_id, meeting_id, identity, now))
    return request_id


def finish_analysis(request_id: str) -> None:
    with analytics.connect() as db:
        db.execute("UPDATE skin_analysis_requests SET finished_at = ? WHERE request_id = ?",
                   (time.time(), request_id))
