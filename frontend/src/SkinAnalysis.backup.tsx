
import React, {
  useEffect,
  useRef,
  useState,
} from "react";

import {
  useRoomContext,
} from "@livekit/components-react";

import {
  Room,
  Track,
} from "livekit-client";

import "./skin-analysis.css";

// ======================================================
// CONFIGURATION
// ======================================================

const API =
  import.meta.env.VITE_API_URL ||
  "http://127.0.0.1:8000";

const GEMINI_TIMEOUT = 90000;
const LOCAL_TIMEOUT = 300000;
const CAMERA_TIMEOUT = 8000;

const MAX_CAMERA_SIDE = 1920;

// ======================================================
// TYPES
// ======================================================

type Observation = {
  feature: string;
  location: string;
  description: string;
};

type Assessment = {
  image_usable: boolean;
  image_quality_note: string;
  observations: Observation[];
  general_care: string[];
  needs_clinician_review: boolean;
  referral_note: string;
};

type VisionResponse = {
  success: boolean;
  status: string;
  meeting_id: string;
  participant_identity: string;
  assessment: Assessment;
  disclaimer: string;
};

type CaptureResponse = {
  success: boolean;
  status: string;
  message: string;
};

type LocalRagReference = {
  source: string;
  page: number | null;
  excerpt: string;
  bm25_score?: number;
  review_status: string;
};

type SkinVoiceGrant = {
  payload: {
    type: string;
    version: number;
    room_name: string;
    participant_id: string;
    face_visible: boolean;
    image_usable: boolean;
    visible_features: string[];
    limited_quality: boolean;
    expires_at: number;
    nonce: string;
  };
  signature: string;
};

type LocalRagResponse = {
  success: boolean;
  status: string;
  development_only: boolean;
  vision_provider: string;

  skin_observations: {
    face_visible: boolean;
    image_usable: boolean;
    visible_features: string[];
    quality_note: string;
    model: string;

    processed_image_size?: {
      width: number;
      height: number;
    };
  };

  medicine_rag: {
    status: string;
    retrieval_query: string;
    total_results: number;
    results: LocalRagReference[];
  };

  disclaimer: string;
  processing_time_seconds?: number;
  voice_grant?: SkinVoiceGrant;
};

type CameraQuality = {
  width: number;
  height: number;
  averageBrightness: number;
  brightnessRange: number;
};

type Props = {
  meetingId: string;
  accessToken: string;
};

type AnalysisMode =
  | "analyze"
  | "vision"
  | "local";

// ======================================================
// ERROR HANDLING
// ======================================================

function getErrorMessage(
  error: unknown,
  mode: AnalysisMode
): string {
  if (
    error instanceof Error &&
    error.name === "AbortError"
  ) {
    if (mode === "local") {
      return (
        "Local Ollama analysis was cancelled or " +
        "exceeded five minutes. Check the backend logs."
      );
    }

    return (
      "Skin analysis timed out. " +
      "The provider may be unavailable."
    );
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Skin Check could not be completed.";
}

// ======================================================
// CAMERA QUALITY CHECK
// ======================================================

function checkCameraQuality(
  context: CanvasRenderingContext2D,
  width: number,
  height: number
): CameraQuality {
  // Analyze a small canvas rather than processing
  // millions of pixels on every camera capture.

  const sampleCanvas =
    document.createElement("canvas");

  sampleCanvas.width = 160;
  sampleCanvas.height = 90;

  const sampleContext =
    sampleCanvas.getContext("2d", {
      willReadFrequently: true,
    });

  if (!sampleContext) {
    throw new Error(
      "Unable to inspect the camera frame."
    );
  }

  sampleContext.drawImage(
    context.canvas,
    0,
    0,
    160,
    90
  );

  const imageData =
    sampleContext.getImageData(
      0,
      0,
      160,
      90
    );

  const pixels = imageData.data;

  let totalBrightness = 0;
  let minBrightness = 255;
  let maxBrightness = 0;
  let count = 0;

  for (
    let index = 0;
    index < pixels.length;
    index += 4
  ) {
    const red = pixels[index];
    const green = pixels[index + 1];
    const blue = pixels[index + 2];

    const brightness =
      (red + green + blue) / 3;

    totalBrightness += brightness;

    minBrightness = Math.min(
      minBrightness,
      brightness
    );

    maxBrightness = Math.max(
      maxBrightness,
      brightness
    );

    count++;
  }

  const averageBrightness =
    count > 0
      ? totalBrightness / count
      : 0;

  const brightnessRange =
    maxBrightness - minBrightness;

  const quality: CameraQuality = {
    width,
    height,
    averageBrightness:
      Number(averageBrightness.toFixed(2)),
    brightnessRange:
      Number(brightnessRange.toFixed(2)),
  };

  console.log(
    "Mateo camera quality:",
    quality
  );

  // These checks reject only extreme cases.
  // They do not measure actual image sharpness.

  if (averageBrightness < 15) {
    throw new Error(
      "Your camera image is too dark. " +
      "Improve your lighting and retry."
    );
  }

  if (brightnessRange < 8) {
    throw new Error(
      "Your camera frame appears blank or " +
      "nearly uniform. Check your webcam."
    );
  }

  return quality;
}

// ======================================================
// LIVEKIT CAMERA CAPTURE
// ======================================================

async function captureCamera(
  room: Room
): Promise<{
  blob: Blob;
  quality: CameraQuality;
}> {
  const publication =
    room.localParticipant.getTrackPublication(
      Track.Source.Camera
    );

  const mediaTrack =
    publication?.track?.mediaStreamTrack;

  if (
    !mediaTrack ||
    mediaTrack.readyState !== "live"
  ) {
    throw new Error(
      "Please turn on your camera before using Skin Check."
    );
  }

  const stream = new MediaStream([
    mediaTrack,
  ]);

  const video =
    document.createElement("video");

  video.muted = true;
  video.autoplay = true;
  video.playsInline = true;
  video.srcObject = stream;

  let timeoutId: number | undefined;

  try {
    await Promise.race([
      new Promise<void>((resolve, reject) => {
        let settled = false;

        const onReady = () => {
          if (
            !settled &&
            video.videoWidth > 0 &&
            video.videoHeight > 0 &&
            video.readyState >= 2
          ) {
            settled = true;
            resolve();
          }
        };

        video.addEventListener(
          "loadeddata",
          onReady
        );

        video.addEventListener(
          "playing",
          onReady
        );

        video.play().then(
          onReady
        ).catch(reject);
      }),

      new Promise<never>((_, reject) => {
        timeoutId = window.setTimeout(
          () => {
            reject(
              new Error(
                "Camera capture timed out. " +
                "Please try again."
              )
            );
          },
          CAMERA_TIMEOUT
        );
      }),
    ]);

    // Allow a few fresh video frames to arrive
// before taking the snapshot.
await new Promise<void>((resolve) => {
  let frames = 0;

  const nextFrame = () => {
    frames += 1;

    if (frames >= 3) {
      resolve();
    } else {
      requestAnimationFrame(nextFrame);
    }
  };

  requestAnimationFrame(nextFrame);
});

    const videoWidth =
      video.videoWidth;

    const videoHeight =
      video.videoHeight;

    if (
      !videoWidth ||
      !videoHeight
    ) {
      throw new Error(
        "No camera frame is available."
      );
    }

    const scale = Math.min(
      1,
      MAX_CAMERA_SIDE /
        Math.max(
          videoWidth,
          videoHeight
        )
    );

    const width = Math.round(
      videoWidth * scale
    );

    const height = Math.round(
      videoHeight * scale
    );

    const canvas =
      document.createElement("canvas");

    canvas.width = width;
    canvas.height = height;

    const context =
      canvas.getContext("2d", {
        willReadFrequently: true,
      });

    if (!context) {
      throw new Error(
        "Could not prepare camera snapshot."
      );
    }

    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";

    context.drawImage(
      video,
      0,
      0,
      width,
      height
    );

    const quality = checkCameraQuality(
      context,
      width,
      height
    );

    const blob = await new Promise<Blob>(
      (resolve, reject) => {
        canvas.toBlob(
          (image) => {
            if (!image) {
              reject(
                new Error(
                  "Could not encode camera image."
                )
              );
              return;
            }

            resolve(image);
          },
          "image/jpeg",
          0.92
        );
      }
    );

    console.log(
      "Mateo camera JPEG:",
      blob.size,
      "bytes"
    );

    return {
      blob,
      quality,
    };

  } finally {
    if (timeoutId !== undefined) {
      window.clearTimeout(timeoutId);
    }

    video.pause();
    video.srcObject = null;
  }
}

// ======================================================
// MAIN COMPONENT
// ======================================================

export default function SkinAnalysis({
  meetingId,
  accessToken,
}: Props) {
  const room = useRoomContext();

  const [consent, setConsent] =
    useState(false);

  const [
    externalConsent,
    setExternalConsent,
  ] = useState(false);

  const [
    localConsent,
    setLocalConsent,
  ] = useState(false);

  const [devToken, setDevToken] =
    useState("");

  const [loading, setLoading] =
    useState(false);

  const [
    currentMode,
    setCurrentMode,
  ] = useState<AnalysisMode | null>(
    null
  );

  const [message, setMessage] =
    useState("");

  const [error, setError] =
    useState("");

  const [result, setResult] =
    useState<VisionResponse | null>(
      null
    );

  const [
    localResult,
    setLocalResult,
  ] = useState<LocalRagResponse | null>(
    null
  );

  const [
    cameraQuality,
    setCameraQuality,
  ] = useState<CameraQuality | null>(
    null
  );

  const controllerRef =
    useRef<AbortController | null>(
      null
    );

  const inFlightRef =
    useRef(false);

  // ==================================================
  // RESET WHEN MEETING CHANGES
  // ==================================================

  useEffect(() => {
    setResult(null);
    setLocalResult(null);
    setCameraQuality(null);

    setMessage("");
    setError("");

    return () => {
      controllerRef.current?.abort();
    };
  }, [meetingId]);

  // ==================================================
  // SUBMIT ANALYSIS
  // ==================================================

  async function submit(
    mode: AnalysisMode
  ) {
    if (inFlightRef.current) {
      return;
    }

    if (!consent) {
      setError(
        "Please consent to capturing your camera image."
      );
      return;
    }

    if (
      mode === "vision" &&
      !externalConsent
    ) {
      setError(
        "Please consent to Gemini processing."
      );
      return;
    }

    if (mode === "local") {
      if (!import.meta.env.DEV) {
        setError(
          "Local analysis is available only in development."
        );
        return;
      }

      if (!localConsent) {
        setError(
          "Please consent to local image processing."
        );
        return;
      }

      if (!devToken.trim()) {
        setError(
          "Please enter your development token."
        );
        return;
      }
    }

    inFlightRef.current = true;

    setLoading(true);
    setCurrentMode(mode);

    setError("");
    setMessage("");

    setResult(null);
    setLocalResult(null);
    setCameraQuality(null);

    const controller =
      new AbortController();

    controllerRef.current =
      controller;

    let timeoutId:
      number | undefined;

    try {
      const captured =
        await captureCamera(room);

      setCameraQuality(
        captured.quality
      );

      if (controller.signal.aborted) {
        return;
      }

      const formData =
        new FormData();

      formData.append(
        "meeting_id",
        meetingId
      );

      formData.append(
        "consent",
        "true"
      );

      formData.append(
        "image",
        captured.blob,
        "camera-snapshot.jpg"
      );

      if (mode === "vision") {
        formData.append(
          "external_processing_consent",
          "true"
        );
      }

      const endpoint =
        mode === "local"
          ? `${API}/api/v1/skin/local-rag`
          : `${API}/api/v1/skin/${mode}`;

      timeoutId =
        window.setTimeout(
          () => controller.abort(),
          mode === "local"
            ? LOCAL_TIMEOUT
            : GEMINI_TIMEOUT
        );

      const response = await fetch(
        endpoint,
        {
          method: "POST",

          headers: {
            Authorization:
              `Bearer ${accessToken}`,

            ...(mode === "local"
              ? {
                  "X-Dev-Token":
                    devToken.trim(),
                }
              : {}),
          },

          body: formData,
          signal: controller.signal,
        }
      );

      let payload: unknown;

      try {
        payload = await response.json();
      } catch {
        throw new Error(
          `Backend returned an invalid response (${response.status}).`
        );
      }

      const data = payload as {
        detail?: unknown;
        message?: string;
      };

      if (!response.ok) {
        let description =
          `Skin Check failed (${response.status}).`;

        if (
          typeof data.detail === "string"
        ) {
          description = data.detail;
        }

        if (
          mode === "vision" &&
          response.status === 429
        ) {
          description =
            "Gemini rate limit reached.";
        }

        if (
          mode === "vision" &&
          response.status === 504
        ) {
          description =
            "Gemini analysis timed out.";
        }

        throw new Error(description);
      }

      // ============================================
      // LOCAL OLLAMA + MEDICINE RAG
      // ============================================

      if (mode === "local") {
        const localData =
          payload as LocalRagResponse;

        if (
          !localData.skin_observations ||
          !localData.medicine_rag
        ) {
          throw new Error(
            "Invalid Local Skin RAG response."
          );
        }

        setLocalResult(localData);

        setMessage(
          "Local Skin RAG analysis completed."
        );

      // ============================================
      // GEMINI VISION
      // ============================================

      } else if (mode === "vision") {
        const visionData =
          payload as VisionResponse;

        if (
          !visionData.assessment
        ) {
          throw new Error(
            "Backend did not return a skin assessment."
          );
        }

        setResult(visionData);

        setMessage(
          "Skin analysis completed successfully."
        );

      // ============================================
      // CAMERA TEST
      // ============================================

      } else {
        const captureData =
          payload as CaptureResponse;

        setMessage(
          captureData.message ||
          "Camera capture verified successfully."
        );
      }

    } catch (err) {
      if (!controller.signal.aborted) {
        setError(
          getErrorMessage(err, mode)
        );
      } else {
        setError(
          getErrorMessage(err, mode)
        );
      }

    } finally {
      if (timeoutId !== undefined) {
        window.clearTimeout(timeoutId);
      }

      controllerRef.current = null;
      inFlightRef.current = false;

      setLoading(false);
      setCurrentMode(null);
    }
  }

  // ==================================================
  // USER INTERFACE
  // ==================================================



async function speakSkinResults() {
  if (!localResult?.voice_grant) {
    setError(
      "No voice authorization available. Run Local Skin RAG again."
    );
    return;
  }

  const grant = localResult.voice_grant;

  if (Date.now() / 1000 >= grant.payload.expires_at) {
    setError(
      "Voice authorization expired. Run Skin Analysis again."
    );
    return;
  }

  if (room.state !== "connected") {
    setError(
      "Please connect to the Mateo meeting first."
    );
    return;
  }

  const confirmed = window.confirm(
    "Mateo will speak your skin observations aloud. " +
    "EVERYONE in the meeting can hear them. Continue?"
  );

  if (!confirmed) {
    return;
  }

  try {
    const encoded = new TextEncoder().encode(
      JSON.stringify({
        voice_grant: grant,
      })
    );

    await room.localParticipant.publishData(
      encoded,
      {
        reliable: true,
        topic: "mateo-skin-voice",
      }
    );

    setError("");
    setMessage(
      "Voice playback requested. Mateo should speak shortly."
    );

  } catch (err) {
    setError(
      err instanceof Error
        ? err.message
        : "Unable to request voice playback."
    );
  }
}

  return (
    <div className="skin-check">

      <h3>
        Skin Check — Private
      </h3>

      <p>
        Analyze{" "}
        <strong>
          one frame from your own camera
        </strong>
        . Other meeting participants are never
        scanned automatically. This is experimental
        visual information, not a medical diagnosis.
      </p>

      {/* ========================================= */}
      {/* CAMERA CONSENT */}
      {/* ========================================= */}

      <label className="skin-consent">
        <input
          type="checkbox"
          checked={consent}
          disabled={loading}
          onChange={(event) =>
            setConsent(
              event.target.checked
            )
          }
        />

        I consent to capturing and submitting
        one image from my camera for this check.
      </label>

      {/* ========================================= */}
      {/* GEMINI CONSENT */}
      {/* ========================================= */}

      <label className="skin-consent">
        <input
          type="checkbox"
          checked={externalConsent}
          disabled={loading}
          onChange={(event) =>
            setExternalConsent(
              event.target.checked
            )
          }
        />

        For Gemini analysis, I additionally
        consent to sending this image to
        Google's Gemini service. Its processing
        and retention are subject to the
        provider's terms. Mateo does not save
        the image in its database.
      </label>

      {/* ========================================= */}
      {/* CAMERA AND GEMINI BUTTONS */}
      {/* ========================================= */}

      <div className="skin-actions">
        <button
          type="button"
          disabled={
            loading || !consent
          }
          onClick={() =>
            void submit("analyze")
          }
        >
          {loading &&
          currentMode === "analyze"
            ? "Testing Camera..."
            : "Test Camera Capture"}
        </button>

        <button
          type="button"
          disabled={
            loading ||
            !consent ||
            !externalConsent
          }
          onClick={() =>
            void submit("vision")
          }
        >
          {loading &&
          currentMode === "vision"
            ? "Processing..."
            : "Analyze Visible Skin Features"}
        </button>
      </div>

      {/* ========================================= */}
      {/* LOCAL SKIN RAG DEVELOPER SECTION */}
      {/* ========================================= */}

      {import.meta.env.DEV && (
        <section
          style={{
            marginTop: 20,
            padding: 16,
            border: "1px solid #475569",
            borderRadius: 12,
          }}
        >
          <h4>
            Local Skin RAG — Developer Preview
          </h4>

          <p>
            Uses your LiveKit camera snapshot
            with local Qwen3-VL Instruct and
            Medicine RAG. References are
            unreviewed development material.
          </p>

          <label className="skin-consent">
            <input
              type="checkbox"
              checked={localConsent}
              disabled={loading}
              onChange={(event) =>
                setLocalConsent(
                  event.target.checked
                )
              }
            />

            I consent to local processing of
            one camera image for this
            developer-only test.
          </label>

          <label
            htmlFor="skin-rag-token"
            style={{
              display: "block",
              marginTop: 12,
            }}
          >
            Development token
          </label>

          <input
            id="skin-rag-token"
            type="password"
            autoComplete="off"
            placeholder="Paste the backend DEV TOKEN"
            value={devToken}
            disabled={loading}
            onChange={(event) =>
              setDevToken(
                event.target.value
              )
            }
            style={{
              maxWidth: 460,
              width: "100%",
              padding: 10,
              marginTop: 6,
            }}
          />

          <div
            className="skin-actions"
            style={{
              marginTop: 14,
            }}
          >
            <button
              type="button"
              disabled={
                loading ||
                !consent ||
                !localConsent ||
                !devToken.trim()
              }
              onClick={() =>
                void submit("local")
              }
            >
              {loading &&
              currentMode === "local"
                ? "Running Local Skin RAG..."
                : "Analyze with Local Ollama + Medicine RAG"}
            </button>
          </div>
        </section>
      )}

      {/* ========================================= */}
      {/* CAMERA QUALITY DETAILS */}
      {/* ========================================= */}

      {cameraQuality && (
        <div
          style={{
            marginTop: 14,
            padding: 12,
            borderRadius: 8,
            border: "1px solid #475569",
          }}
        >
          <strong>
            Camera Capture Quality
          </strong>

          <p>
            Resolution:{" "}
            {cameraQuality.width}
            {" × "}
            {cameraQuality.height}
          </p>

          <p>
            Average brightness:{" "}
            {cameraQuality.averageBrightness}
          </p>

          <p>
            Brightness range:{" "}
            {cameraQuality.brightnessRange}
          </p>
        </div>
      )}

      {/* ========================================= */}
      {/* PROCESSING STATUS */}
      {/* ========================================= */}

      {loading && (
        <p
          role="status"
          style={{
            marginTop: 12,
            color: "#a9c7ef",
          }}
        >
          {currentMode === "vision"
            ? "Analyzing with Gemini..."
            : currentMode === "local"
              ? "Running local Qwen3-VL and Medicine RAG. CPU inference may take several minutes..."
              : "Verifying your camera snapshot..."}
        </p>
      )}

      {/* ========================================= */}
      {/* SUCCESS MESSAGE */}
      {/* ========================================= */}

      {message && (
        <p
          role="status"
          className="skin-success"
        >
          {message}
        </p>
      )}

      {/* ========================================= */}
      {/* ERROR MESSAGE */}
      {/* ========================================= */}

      {error && (
        <p
          role="alert"
          className="skin-error"
        >
          {error}
        </p>
      )}

      {/* ========================================= */}
      {/* LOCAL OLLAMA RESULTS */}
      {/* ========================================= */}

      {localResult && (
        <div className="skin-results">

          <h4>
            Local Vision Observations
            (Not a Diagnosis)
          </h4>


{localResult.voice_grant && (
  <div style={{ margin: "14px 0" }}>
    <button
      type="button"
      disabled={loading}
      onClick={() => void speakSkinResults()}
    >
      Speak Results to Meeting
    </button>

    <p style={{ fontSize: 13, color: "#fbbf24" }}>
      Everyone in this meeting may hear
      Mateo's spoken summary.
    </p>
  </div>
)}


          <p>
            <strong>
              Model:
            </strong>{" "}
            {localResult.skin_observations.model}
          </p>

          <p>
            <strong>
              Image quality:
            </strong>{" "}
            {localResult.skin_observations.quality_note}
          </p>

          <p>
            <strong>
              Face visible:
            </strong>{" "}
            {String(
              localResult.skin_observations.face_visible
            )}
          </p>

          <p>
            <strong>
              Image usable:
            </strong>{" "}
            {String(
              localResult.skin_observations.image_usable
            )}
          </p>

          <p>
            <strong>
              Visible features:
            </strong>{" "}
            {localResult.skin_observations.visible_features.join(
              ", "
            ) || "None identified"}
          </p>

          {typeof localResult.processing_time_seconds ===
            "number" && (
            <p>
              <strong>
                Processing time:
              </strong>{" "}
              {localResult.processing_time_seconds}
              {" seconds"}
            </p>
          )}

          <h4>
            Development Medicine RAG References
          </h4>

          <p>
            Review status:{" "}
            {localResult.medicine_rag.status}
            {" — not medically approved."}
          </p>

          {localResult.medicine_rag.results.length > 0 ? (
            <ul>
              {localResult.medicine_rag.results.map(
                (item, index) => (
                  <li
                    key={`${item.source}-${index}`}
                    style={{
                      marginBottom: 14,
                    }}
                  >
                    <strong>
                      {item.source}
                    </strong>

                    {" ("}
                    {item.review_status}
                    {")"}

                    <p
                      style={{
                        whiteSpace: "pre-wrap",
                        overflowWrap: "anywhere",
                      }}
                    >
                      {item.excerpt.replace(
                        /\[\[|\]\]/g,
                        ""
                      )}
                    </p>
                  </li>
                )
              )}
            </ul>
          ) : (
            <p>
              No medicine references matched
              the visible features.
            </p>
          )}

          <p className="skin-disclaimer">
            {localResult.disclaimer}
          </p>

        </div>
      )}

      {/* ========================================= */}
      {/* EXISTING GEMINI RESULTS */}
      {/* ========================================= */}

      {result && (
        <div className="skin-results">

          <h4>
            Visual Observations
            (Not a Diagnosis)
          </h4>

          <p>
            <strong>
              Image quality:
            </strong>{" "}
            {result.assessment.image_quality_note}
          </p>

          {result.assessment.image_usable ? (
            result.assessment.observations?.length ? (
              <ul>
                {result.assessment.observations.map(
                  (observation, index) => (
                    <li key={index}>
                      <strong>
                        {observation.feature}
                      </strong>

                      {" — "}
                      {observation.location}
                      {": "}
                      {observation.description}
                    </li>
                  )
                )}
              </ul>
            ) : (
              <p>
                No clearly distinguishable
                skin features were identified.
                This does not rule out a condition.
              </p>
            )
          ) : (
            <p>
              This image is not suitable for
              a reliable visual description.
              Try better lighting and a clear image.
            </p>
          )}

          {result.assessment.general_care?.length > 0 && (
            <>
              <h4>
                General Non-Medicated Care
              </h4>

              <ul>
                {result.assessment.general_care.map(
                  (tip, index) => (
                    <li key={index}>
                      {tip}
                    </li>
                  )
                )}
              </ul>
            </>
          )}

          <p>
            <strong>
              Professional review:
            </strong>{" "}
            {result.assessment.referral_note}
          </p>

          <p className="skin-disclaimer">
            {result.disclaimer}
          </p>

          <p className="skin-disclaimer">
            If you experience difficulty
            breathing or swallowing, or sudden
            swelling involving your lips or tongue,
            seek emergency medical assistance.
          </p>

        </div>
      )}

    </div>
  );
}
