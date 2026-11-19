
"""Mateo Skin Check: signed, non-diagnostic voice grants."""

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any


VOICE_TOPIC = "mateo-skin-voice"
VOICE_GRANT_SECONDS = 300

FEATURE_NAMES = {
    "redness": "redness",
    "bumps": "small bumps",
    "dryness": "dryness",
    "peeling": "peeling",
    "rash": "a rash-like appearance",
}


def _signing_key() -> bytes:
    key = os.getenv("AGENT_EVENT_KEY", "")
    if not key:
        raise RuntimeError(
            "AGENT_EVENT_KEY must be configured "
            "for both FastAPI and the LiveKit agent."
        )
    return key.encode("utf-8")


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def create_skin_voice_grant(
    *,
    room_name: str,
    participant_id: str,
    observations: dict[str, Any],
    medical_explanation: str = "",
) -> dict:
    
    if not room_name or not participant_id.startswith("human_"):
        raise ValueError("Invalid participant or room.")

    features = observations.get("visible_features", [])
    if not isinstance(features, list):
        features = []

    safe_features = []
    for value in features:
        if isinstance(value, str):
            name = value.strip().lower()
            if name in FEATURE_NAMES and name not in safe_features:
                safe_features.append(name)

    quality_note = str(
        observations.get("quality_note") or ""
    ).lower()

    limited_quality = any(
        word in quality_note
        for word in (
            "blur",
            "unclear",
            "low quality",
            "low resolution",
            "poor quality",
        )
    )

    payload = {
        "type": "skin_voice_grant",
        "version": 1,
        "room_name": room_name,
        "participant_id": participant_id,
        "face_visible": observations.get("face_visible") is True,
        "image_usable": observations.get("image_usable") is True,
        "visible_features": safe_features,
        "limited_quality": limited_quality,
        "medical_explanation": (
    medical_explanation[:2000]
    if isinstance(medical_explanation, str)
    else ""
),
        "expires_at": int(time.time()) + VOICE_GRANT_SECONDS,
        "nonce": secrets.token_urlsafe(18),
    }

    signature = hmac.new(
        _signing_key(),
        b"mateo-skin-voice-v1:" + _canonical_json(payload),
        hashlib.sha256,
    ).hexdigest()

    return {
        "payload": payload,
        "signature": signature,
    }


def verify_skin_voice_grant(
    grant: Any,
    *,
    room_name: str,
    participant_id: str,
) -> dict | None:
    if not isinstance(grant, dict):
        return None

    payload = grant.get("payload")
    signature = grant.get("signature")

    if not isinstance(payload, dict) or not isinstance(signature, str):
        return None

    if payload.get("type") != "skin_voice_grant":
        return None

    if payload.get("version") != 1:
        return None

    if payload.get("room_name") != room_name:
        return None

    if payload.get("participant_id") != participant_id:
        return None

    expiry = payload.get("expires_at")

    if type(expiry) is not int:
        return None

    if not int(time.time()) <= expiry <= int(time.time()) + VOICE_GRANT_SECONDS:
        return None

    nonce = payload.get("nonce")

    if not isinstance(nonce, str) or not (16 <= len(nonce) <= 80):
        return None

    if type(payload.get("face_visible")) is not bool:
        return None

    if type(payload.get("image_usable")) is not bool:
        return None

    if type(payload.get("limited_quality")) is not bool:
        return None

    features = payload.get("visible_features")

    if (
        not isinstance(features, list)
        or len(features) > len(FEATURE_NAMES)
        or any(
            not isinstance(f, str) or f not in FEATURE_NAMES
            for f in features
        )
    ):
        return None

    explanation = payload.get("medical_explanation", "")

    if (
        not isinstance(explanation, str)
        or len(explanation) > 2000
    ):
        return None

    

    expected = hmac.new(
        _signing_key(),
        b"mateo-skin-voice-v1:" + _canonical_json(payload),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        return None

    return payload



def build_skin_voice_summary(payload: dict) -> str:
    if not payload["face_visible"]:
        return (
            "The submitted image did not show a clearly "
            "visible face. Please capture another image."
        )

    if not payload["image_usable"]:
        return (
            "The skin image was not clear enough for "
            "reliable visual observations. "
            "Please try a clearer image."
        )

    descriptions = [
        FEATURE_NAMES[f]
        for f in payload["visible_features"]
    ]

    if not descriptions:
        feature_sentence = (
            "I could not identify any clear skin features. "
        )
    elif len(descriptions) == 1:
        feature_sentence = (
            f"The image may show {descriptions[0]}. "
        )
    else:
        joined = (
            ", ".join(descriptions[:-1])
            + " and "
            + descriptions[-1]
        )
        feature_sentence = (
            f"The image may show {joined}. "
        )

    text = "I have completed the skin image check. "
    text += feature_sentence

    if payload["limited_quality"]:
        text += (
            "The image quality is limited, so these "
            "observations may be inaccurate. "
        )

    medical_explanation = payload.get(
        "medical_explanation", ""
    )

    if (
        isinstance(medical_explanation, str)
        and medical_explanation.strip()
    ):
        text += medical_explanation.strip()
    else:
        text += (
            "These are experimental visual observations, "
            "not a medical diagnosis. "
            "A healthcare professional should assess "
            "persistent or concerning changes."
        )

    return text