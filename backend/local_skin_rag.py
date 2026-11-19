
"""
Mateo AI - Local Skin Vision + Medicine RAG

Developer-only pipeline:

LiveKit camera image
    -> Image validation
    -> Blank/dark frame detection
    -> Ollama Qwen3-VL
    -> Visible skin observations
    -> SQLite Medicine RAG (FTS5/BM25)
    -> Structured JSON response

No skin disease diagnosis.
No automatic medicine recommendations.
Medicine documents remain DRAFT_UNREVIEWED.
"""

import argparse
import base64
import io
import json
import logging
import os
import re
import socket
import statistics
import time
import urllib.error
import urllib.request

from pathlib import Path

from backend.medicine_rerank import retrieve_ranked_references

from typing import Any

from PIL import (
    Image,
    ImageOps,
    UnidentifiedImageError,
)

from backend.medicine_dev_index import (
    DB_PATH,
    search_documents,
)

# =====================================================
# CONFIGURATION
# =====================================================

logger = logging.getLogger("mateo-local-skin-rag")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

OLLAMA_MODEL = os.getenv(
    "MATEO_LOCAL_VISION_MODEL",
    "qwen3-vl:2b-instruct",
)

OLLAMA_API = os.getenv(
    "MATEO_OLLAMA_API",
    "http://127.0.0.1:11434/api/chat",
)

OLLAMA_TIMEOUT = int(
    os.getenv("MATEO_OLLAMA_TIMEOUT", "270")
)

MAX_IMAGE_DIMENSION = int(
    os.getenv("MATEO_IMAGE_DIMENSION", "384")
)

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000

MAX_OUTPUT_TOKENS = int(
    os.getenv("MATEO_VISION_TOKENS", "512")
)

OLLAMA_CONTEXT_SIZE = int(
    os.getenv("MATEO_OLLAMA_CONTEXT", "2048")
)

OLLAMA_THREADS = int(
    os.getenv("MATEO_OLLAMA_THREADS", "4")
)

OLLAMA_KEEP_ALIVE = os.getenv("MATEO_OLLAMA_KEEP_ALIVE", "15m")

# Conservative image-quality thresholds.
# These reject obviously unusable images, not
# every image that might be difficult to assess.
MIN_BRIGHTNESS = 12.0
MIN_CONTRAST_STD = 4.0

ALLOWED_FEATURES = {
    "redness",
    "dryness",
    "peeling",
    "bumps",
    "rash",
}

# =====================================================
# STRUCTURED OUTPUT SCHEMA
# =====================================================

VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "face_visible": {
            "type": "boolean",
        },
        "image_usable": {
            "type": "boolean",
        },
        "visible_features": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "redness",
                    "dryness",
                    "peeling",
                    "bumps",
                    "rash",
                ],
            },
        },
        "quality_note": {
            "type": "string",
        },
    },
    "required": [
        "face_visible",
        "image_usable",
        "visible_features",
        "quality_note",
    ],
    "additionalProperties": False,
}

# =====================================================
# SHORT VISION PROMPT
# =====================================================

VISION_PROMPT = """
Inspect this image for visible facial skin features.

Return JSON only.

Fields:
- face_visible: boolean
- image_usable: boolean
- visible_features: list of visible features
- quality_note: short image-quality description

Allowed features:
redness, dryness, peeling, bumps, rash.

If features are unclear, return an empty list.
If no face is visible, set face_visible to false.

Describe only visible features.
Do not diagnose conditions.
Do not recommend medication.
Do not identify the person.
Do not infer pain or itching.
"""

# =====================================================
# IMAGE QUALITY VALIDATION
# =====================================================

def image_quality_metrics(
    image: Image.Image,
) -> dict[str, float]:
    """
    Estimate frame brightness and contrast.

    Uses a small grayscale thumbnail so that
    validation is inexpensive on CPU.
    """

    sample = ImageOps.grayscale(image)

    sample.thumbnail(
        (128, 128),
        Image.Resampling.BILINEAR,
    )

    pixels = list(sample.get_flattened_data())

    if not pixels:
        raise ValueError(
            "Camera image has no pixels."
        )

    mean_brightness = statistics.fmean(
        pixels
    )

    contrast_std = (
        statistics.pstdev(pixels)
        if len(pixels) > 1
        else 0.0
    )

    return {
        "mean_brightness": round(
            mean_brightness,
            2,
        ),
        "contrast_std": round(
            contrast_std,
            2,
        ),
    }

def validate_camera_frame(
    metrics: dict[str, float],
) -> None:
    """
    Reject only obviously unusable frames.

    A low-detail frame may be valid, so these
    checks deliberately use low thresholds.
    """

    brightness = metrics["mean_brightness"]
    contrast = metrics["contrast_std"]

    if brightness < MIN_BRIGHTNESS:
        raise ValueError(
            "Camera frame is extremely dark. "
            "Turn on a light and retry Skin Check."
        )

    if contrast < MIN_CONTRAST_STD:
        raise ValueError(
            "Camera frame appears blank or nearly "
            "uniform. Check your LiveKit video "
            "preview and retry."
        )

# =====================================================
# IMAGE PREPARATION
# =====================================================

def prepare_image(
    image_bytes: bytes,
) -> tuple[bytes, dict[str, int]]:

    if not image_bytes:
        raise ValueError(
            "Image is empty."
        )

    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(
            "Image exceeds the 5 MB limit."
        )

    try:
        with Image.open(
            io.BytesIO(image_bytes)
        ) as source:

            if (
                source.width * source.height
                > MAX_IMAGE_PIXELS
            ):
                raise ValueError(
                    "Image resolution is too large."
                )

            source.load()

            image = ImageOps.exif_transpose(
                source
            ).convert("RGB")

    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
    ) as exc:
        raise ValueError(
            "Invalid or unsupported camera image."
        ) from exc

    metrics = image_quality_metrics(image)

    logger.info(
        "Camera quality: brightness=%.2f, "
        "contrast_std=%.2f",
        metrics["mean_brightness"],
        metrics["contrast_std"],
    )

    validate_camera_frame(metrics)

    image.thumbnail(
        (
            MAX_IMAGE_DIMENSION,
            MAX_IMAGE_DIMENSION,
        ),
        Image.Resampling.LANCZOS,
    )

    output = io.BytesIO()

    image.save(
        output,
        format="JPEG",
        quality=85,
        optimize=True,
    )

    processed = output.getvalue()

    size = {
        "width": image.width,
        "height": image.height,
    }

    logger.info(
        "Image resized to %sx%s (%s bytes).",
        image.width,
        image.height,
        len(processed),
    )

    if len(processed) < 2000:
        logger.warning(
            "Processed image is unusually small "
            "(%s bytes). Review camera preview.",
            len(processed),
        )

    return processed, size

# =====================================================
# JSON RESPONSE PARSING
# =====================================================

def parse_model_json(
    content: str,
) -> dict[str, Any]:

    content = content.strip()

    if not content:
        raise RuntimeError(
            "Ollama returned empty JSON content."
        )

    content = re.sub(
        r"^```(?:json)?\s*",
        "",
        content,
        flags=re.IGNORECASE,
    )

    content = re.sub(
        r"\s*```$",
        "",
        content,
    )

    try:
        parsed = json.loads(content)

        if isinstance(parsed, dict):
            return parsed

    except json.JSONDecodeError:
        pass

    # Some models put text around their JSON.
    # Recover a complete JSON object, if present.
    decoder = json.JSONDecoder()

    for match in re.finditer(
        r"\{",
        content,
    ):
        try:
            candidate, _ = decoder.raw_decode(
                content[match.start():]
            )

            if isinstance(candidate, dict):
                return candidate

        except json.JSONDecodeError:
            continue

    raise RuntimeError(
        "Vision model returned invalid JSON."
    )

# =====================================================
# OLLAMA INFERENCE
# =====================================================

def call_ollama(
    image_bytes: bytes,
) -> dict[str, Any]:

    encoded_image = base64.b64encode(
        image_bytes
    ).decode("ascii")

    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": VISION_SCHEMA,
        "think": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": 0,
            "num_predict": MAX_OUTPUT_TOKENS,
            "num_ctx": OLLAMA_CONTEXT_SIZE,
            "num_thread": OLLAMA_THREADS,
        },
        "messages": [
            {
                "role": "user",
                "content": VISION_PROMPT,
                "images": [
                    encoded_image,
                ],
            }
        ],
    }

    request = urllib.request.Request(
        OLLAMA_API,
        data=json.dumps(
            payload
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    logger.info(
        "Running %s on local Ollama...",
        OLLAMA_MODEL,
    )

    started = time.monotonic()

    try:
        with urllib.request.urlopen(
            request,
            timeout=OLLAMA_TIMEOUT,
        ) as response:

            raw_response = response.read()

    except urllib.error.HTTPError as exc:

        detail = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        logger.error(
            "Ollama HTTP %s: %s",
            exc.code,
            detail[:500],
        )

        raise RuntimeError(
            f"Ollama returned HTTP {exc.code}."
        ) from exc

    except (
        urllib.error.URLError,
        socket.timeout,
        TimeoutError,
    ) as exc:

        logger.error(
            "Ollama connection or timeout error: %s",
            exc,
        )

        raise RuntimeError(
            "Ollama is unavailable or inference "
            "exceeded the server timeout."
        ) from exc

    elapsed = time.monotonic() - started

    logger.info(
        "Ollama responded in %.1f seconds.",
        elapsed,
    )

    try:
        result = json.loads(
            raw_response.decode("utf-8")
        )

    except (
        ValueError,
        UnicodeDecodeError,
    ) as exc:
        raise RuntimeError(
            "Ollama API returned invalid JSON."
        ) from exc

    if not isinstance(result, dict):
        raise RuntimeError(
            "Unexpected Ollama API response."
        )

    message = result.get("message") or {}

    if not isinstance(message, dict):
        message = {}

    content = message.get("content") or ""
    thinking = message.get("thinking") or ""

    logger.info(
        "Ollama diagnostics: done_reason=%s, "
        "eval_count=%s, "
        "prompt_eval_count=%s, "
        "thinking_length=%s, "
        "content_length=%s",
        result.get("done_reason"),
        result.get("eval_count"),
        result.get("prompt_eval_count"),
        len(str(thinking)),
        len(str(content)),
    )

    logger.info(
        "Vision timing: load=%.2fs, prompt=%.2fs, generation=%.2fs",
        (result.get("load_duration") or 0) / 1e9,
        (result.get("prompt_eval_duration") or 0) / 1e9,
        (result.get("eval_duration") or 0) / 1e9,
    )

    if not isinstance(content, str):
        raise RuntimeError(
            "Ollama returned non-text content."
        )

    if not content.strip():

        reason = result.get(
            "done_reason"
        )

        if thinking:
            logger.warning(
                "Model generated reasoning but "
                "did not produce final JSON."
            )

        if reason == "length":
            raise RuntimeError(
                "Ollama exhausted its token budget "
                "before producing JSON. The model "
                "may still be reasoning."
            )

        raise RuntimeError(
            "Ollama produced no final response. "
            f"Reason: {reason}"
        )

    parsed = parse_model_json(content)

    required = {
        "face_visible",
        "image_usable",
        "visible_features",
        "quality_note",
    }

    if not required.issubset(parsed):
        raise RuntimeError(
            "Vision JSON is missing required fields."
        )

    if (
        not isinstance(parsed["face_visible"], bool)
        or not isinstance(parsed["image_usable"], bool)
        or not isinstance(
            parsed["visible_features"],
            list,
        )
        or not isinstance(
            parsed["quality_note"],
            str,
        )
    ):
        raise RuntimeError(
            "Vision JSON contains invalid field types."
        )

    return parsed

# =====================================================
# FEATURE NORMALIZATION
# =====================================================

def normalize_features(
    raw_features: Any,
) -> list[str]:

    if not isinstance(
        raw_features,
        list,
    ):
        return []

    aliases = {
        "acne": "bumps",
        "pimple": "bumps",
        "pimples": "bumps",
        "spots": "bumps",
        "red": "redness",
        "flaking": "peeling",
        "flaky": "peeling",
        "dry": "dryness",
    }

    features: list[str] = []

    for item in raw_features:

        if not isinstance(
            item,
            str,
        ):
            continue

        normalized = (
            item.strip().lower()
        )

        normalized = aliases.get(
            normalized,
            normalized,
        )

        if (
            normalized in ALLOWED_FEATURES
            and normalized not in features
        ):
            features.append(
                normalized
            )

    return features

# =====================================================
# IMAGE ANALYSIS
# =====================================================

def analyze_local_image(
    image_bytes: bytes,
) -> dict[str, Any]:

    processed_image, image_size = (
        prepare_image(image_bytes)
    )

    model_result = call_ollama(
        processed_image
    )

    face_visible = (
        model_result.get("face_visible")
        is True
    )

    image_usable = (
        model_result.get("image_usable")
        is True
    )

    features = normalize_features(
        model_result.get(
            "visible_features",
            [],
        )
    )

    if (
        not face_visible
        or not image_usable
    ):
        features = []

    quality_note = model_result.get(
        "quality_note",
        "",
    )

    if not isinstance(
        quality_note,
        str,
    ):
        quality_note = ""

    if not quality_note.strip():
        quality_note = (
            "Image quality could not "
            "be determined."
        )

    return {
        "face_visible": face_visible,
        "image_usable": image_usable,
        "visible_features": features,
        "quality_note": quality_note.strip()[:200],
        "model": OLLAMA_MODEL,
        "processed_image_size": image_size,
    }

# =====================================================
# MEDICINE RAG RETRIEVAL
# =====================================================


def retrieve_medicine_references(
    features: list[str],
    limit: int = 5,
) -> dict[str, Any]:
    """
    Retrieve ranked and deduplicated medicine references.

    Development-only. Sources remain unreviewed.
    """
    return retrieve_ranked_references(
        features=features,
        limit=limit,
    )

# =====================================================
# COMPLETE PIPELINE
# =====================================================

def analyze_skin_and_retrieve(
    image_bytes: bytes,
    reference_retriever=None,
) -> dict[str, Any]:

    started = time.monotonic()

    logger.info(
        "Starting local skin vision + RAG pipeline."
    )

    observations = analyze_local_image(
        image_bytes
    )
    vision_finished = time.monotonic()

    if (
        observations["face_visible"]
        and observations["image_usable"]
    ):
        features = observations[
            "visible_features"
        ]
    else:
        features = []

    medicine_results = (
        (reference_retriever or retrieve_medicine_references)(
            features,
            limit=5,
        )
    )

    elapsed = (
        time.monotonic()
        - started
    )

    logger.info(
        "Local skin pipeline completed "
        "in %.1f seconds.",
        elapsed,
    )

    return {
        "success": True,
        "status": "development_local_skin_rag",
        "development_only": True,
        "vision_provider": "local_ollama",
        "skin_observations": observations,
        "medicine_rag": medicine_results,
        "disclaimer": (
            "Experimental visual observations only. "
            "Not a medical diagnosis. "
            "These references are unreviewed "
            "development materials, not medical "
            "or treatment recommendations."
        ),
        "processing_time_seconds": round(
            elapsed,
            1,
        ),
        "timings": {
            "vision_seconds": round(vision_finished - started, 2),
            "retrieval_seconds": round(time.monotonic() - vision_finished, 2),
        },
    }

# =====================================================
# COMMAND-LINE TEST
# =====================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Mateo Local Skin Vision "
            "and Medicine RAG"
        )
    )

    parser.add_argument(
        "--image",
        required=True,
        help="Path to JPEG, PNG or WebP image.",
    )

    args = parser.parse_args()

    image_path = Path(
        args.image
    )

    if not image_path.is_file():
        raise SystemExit(
            f"Image not found: {image_path}"
        )

    result = (
        analyze_skin_and_retrieve(
            image_path.read_bytes()
        )
    )

    print()

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )

if __name__ == "__main__":
    main()
