
"""
Mateo AI - Local Skin RAG Developer Preview

Localhost-only development interface.
Uses Ollama + existing Medicine RAG.
Draft references are not patient-facing.
"""

import asyncio
import logging

from fastapi import (
    APIRouter,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse

from backend.local_skin_rag import (
    analyze_skin_and_retrieve,
)
from backend.medicine_dev_routes import (
    verify_dev_access,
)

logger = logging.getLogger("mateo-skin-preview")

router = APIRouter(
    prefix="/api/v1/medicine",
    tags=["Skin RAG Developer Preview"],
)

MAX_UPLOAD = 5 * 1024 * 1024

ALLOWED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}

def require_localhost(request: Request):
    host = request.client.host if request.client else ""

    if host not in {"127.0.0.1", "::1"}:
        raise HTTPException(
            status_code=403,
            detail="Local developer access only.",
        )

@router.get(
    "/dev-preview",
    response_class=HTMLResponse,
)
def show_preview(request: Request):
    require_localhost(request)

    return HTMLResponse(
        content=PREVIEW_HTML,
        headers={"Cache-Control": "no-store"},
    )

@router.post("/dev-preview/analyze")
async def analyze_preview(
    request: Request,
    image: UploadFile = File(...),
    x_dev_token: str | None = Header(
        default=None,
        alias="X-Dev-Token",
    ),
):
    verify_dev_access(request, x_dev_token)

    if image.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Upload a PNG, JPEG or WebP image.",
        )

    try:
        data = await image.read(MAX_UPLOAD + 1)
    finally:
        await image.close()

    if not data or len(data) > MAX_UPLOAD:
        raise HTTPException(
            status_code=413,
            detail="Image must be nonempty and at most 5 MB.",
        )

    try:
        result = await asyncio.to_thread(
            analyze_skin_and_retrieve,
            data,
        )
    except (ValueError, RuntimeError) as exc:
        logger.warning(
            "Developer skin preview failed: %s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Local image analysis failed. "
                "Check Ollama, the model, "
                "and the Medicine RAG index."
            ),
        ) from exc

    return result

PREVIEW_HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1">
<title>Mateo Skin RAG Developer Preview</title>

<style>
* { box-sizing: border-box; }

body {
  font-family: system-ui, Arial, sans-serif;
  background: #101827;
  color: #edf2fa;
  margin: 0;
  padding: 28px 16px;
}

main {
  max-width: 850px;
  margin: auto;
}

h1 { margin-bottom: 8px; }

.subtitle {
  color: #acb9cc;
  line-height: 1.6;
}

.panel {
  background: #192638;
  border: 1px solid #314259;
  border-radius: 16px;
  padding: 22px;
  margin: 20px 0;
}

label {
  display: block;
  margin: 14px 0 8px;
  font-weight: 600;
}

input {
  width: 100%;
  padding: 12px;
  border: 1px solid #60738c;
  border-radius: 8px;
  background: #101827;
  color: white;
}

input[type=file] { cursor: pointer; }

button {
  margin-top: 18px;
  padding: 13px 22px;
  background: #3979dc;
  color: white;
  border: 0;
  border-radius: 9px;
  font-size: 15px;
  font-weight: 650;
  cursor: pointer;
}

button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

img {
  display: block;
  max-width: 100%;
  max-height: 350px;
  border-radius: 12px;
  margin-top: 15px;
}

.notice {
  padding: 12px;
  border: 1px solid #7b6942;
  border-radius: 8px;
  background: #292719;
  color: #ffe5a2;
  margin: 16px 0;
}

.result {
  padding: 15px;
  background: #121e2d;
  border: 1px solid #33445c;
  border-radius: 10px;
  margin-top: 12px;
}

.meta {
  color: #9fafc3;
  font-size: 13px;
}

pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: inherit;
}

#error { color: #ffaaa9; }

#status { color: #b3c9ef; }

[hidden] { display: none !important; }
</style>
</head>

<body>
<main>

<h1>Mateo AI — Skin RAG</h1>

<p class="subtitle">
Local developer preview using Qwen3-VL and
your existing Medicine RAG documents.
</p>

<div class="notice">
DEVELOPMENT ONLY — Medicine references are
unreviewed. Results are not diagnoses,
prescriptions, or personalized medical advice.
</div>

<section class="panel">

<h2>Analyze a test image</h2>

<label for="token">
Development token
</label>

<input
  id="token"
  type="password"
  autocomplete="off"
  placeholder="Paste your backend development token"
>

<label for="image">
Upload facial image
</label>

<input
  id="image"
  type="file"
  accept="image/png,image/jpeg,image/webp"
>

<img
  id="preview"
  alt="Selected test image"
  hidden
>

<button id="analyze" type="button">
Analyze Skin
</button>

<p id="status" role="status"></p>
<p id="error" role="alert"></p>

</section>

<section id="results" class="panel" hidden>

<h2>Visual observations</h2>

<div id="observations" class="result"></div>

<h2>Medicine RAG references</h2>

<p class="meta">
Source excerpts from your local development index.
</p>

<div id="references"></div>

</section>

</main>

<script>
const tokenInput = document.getElementById("token");
const imageInput = document.getElementById("image");
const preview = document.getElementById("preview");
const button = document.getElementById("analyze");
const statusEl = document.getElementById("status");
const errorEl = document.getElementById("error");
const resultsEl = document.getElementById("results");
const observationsEl =
  document.getElementById("observations");
const referencesEl =
  document.getElementById("references");

let previewUrl = null;

imageInput.addEventListener("change", () => {
  const file = imageInput.files[0];

  resultsEl.hidden = true;
  errorEl.textContent = "";

  if (previewUrl) {
    URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }

  preview.hidden = !file;

  if (file) {
    previewUrl = URL.createObjectURL(file);
    preview.src = previewUrl;
  }
});

function addText(parent, tag, value, className = "") {
  const el = document.createElement(tag);
  el.textContent = String(value ?? "");

  if (className) el.className = className;

  parent.appendChild(el);
  return el;
}

function displayResult(data) {
  resultsEl.hidden = false;

  observationsEl.replaceChildren();
  referencesEl.replaceChildren();

  const vision = data.skin_observations || {};
  const rag = data.medicine_rag || {};

  addText(
    observationsEl,
    "p",
    "Face visible: " + Boolean(vision.face_visible)
  );

  addText(
    observationsEl,
    "p",
    "Image usable: " + Boolean(vision.image_usable)
  );

  const features = vision.visible_features || [];

  addText(
    observationsEl,
    "p",
    "Visible features: " +
      (features.length ? features.join(", ") : "None")
  );

  addText(
    observationsEl,
    "p",
    "Image quality: " + (vision.quality_note || "Not provided")
  );

  const records = rag.results || [];

  addText(
    referencesEl,
    "p",
    records.length + " development reference(s) retrieved."
  );

  if (!records.length) {
    addText(
      referencesEl,
      "p",
      "No matching references. No medical conclusion is made."
    );
  }

  for (const record of records) {
    const card = document.createElement("div");
    card.className = "result";

    addText(card, "h3", record.source || "Unknown source");

    addText(
      card,
      "p",
      "Review status: " + record.review_status,
      "meta"
    );

    addText(
      card,
      "pre",
      record.excerpt || ""
    );

    referencesEl.appendChild(card);
  }
}

button.addEventListener("click", async () => {
  const token = tokenInput.value.trim();
  const file = imageInput.files[0];

  if (!token || !file) {
    errorEl.textContent =
      "Enter the development token and select an image.";
    return;
  }

  if (file.size > 5 * 1024 * 1024) {
    errorEl.textContent = "Image must be 5 MB or smaller.";
    return;
  }

  button.disabled = true;
  resultsEl.hidden = true;
  errorEl.textContent = "";

  statusEl.textContent =
    "Analyzing locally with Ollama. " +
    "CPU inference may be slow. Keep this page open.";

  const form = new FormData();
  form.append("image", file);

  try {
    const response = await fetch(
      "/api/v1/medicine/dev-preview/analyze",
      {
        method: "POST",
        headers: {
          "X-Dev-Token": token
        },
        body: form,
        cache: "no-store"
      }
    );

    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        data.detail || "Analysis failed."
      );
    }

    displayResult(data);

    statusEl.textContent =
      "Analysis finished. " +
      "All references are development-only.";

  } catch (error) {
    errorEl.textContent =
      error.message || "Request failed.";
    statusEl.textContent = "";
  } finally {
    button.disabled = false;
  }
});
</script>

</body>
</html>
"""