
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
  RoomEvent,
  ParticipantKind,
  Track,
  ConnectionState,
  type RemoteParticipant,
} from "livekit-client";

import "./skin-analysis.css";
import RagDocumentUpload from "./RagDocumentUpload";
import { meetingApiUrl } from "./meetingUrls";

const CAMERA_TIMEOUT = 8000;
const CAMERA_REQUEST_TIMEOUT = 30000;

// Ollama inference on CPU can take over 100 seconds.
const LOCAL_RAG_TIMEOUT = 300000;
const VOICE_TOPIC = "mateo-skin-voice";

type VoicePlayback = "idle" | "requesting" | "accepted" | "played";
type VoiceRequest = { id: string; agentIdentity: string; timer?: number };

const voiceErrors: Record<string, string> = {
  not_ready: "Mateo is still joining. Try again shortly.",
  invalid_grant: "Voice authorization is no longer valid. Run Skin Check again.",
  busy: "Mateo is already speaking a Skin Check summary. Try again when it finishes.",
  interrupted: "Mateo's summary was interrupted. You can retry.",
  unavailable: "Mateo couldn't play the summary. You can retry.",
};


// =====================================================
// TYPES
// =====================================================

type Props = {
  meetingId: string;
  accessToken: string;
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

type SkinObservations = {
  face_visible: boolean;
  image_usable: boolean;
  visible_features: string[];
  quality_note: string;
  model?: string;

  processed_image_size?: {
    width: number;
    height: number;
  };
};

type MedicineReference = {
  source: string;
  page: number | null;
  excerpt: string;
  bm25_score?: number;
  review_status: string;
};

type MedicineRagResult = {
  status: string;
  retrieval_query: string;
  total_results: number;
  results: MedicineReference[];
};

type LocalRagResponse = {
  success: boolean;
  status: string;
  development_only: boolean;
  vision_provider: string;

  skin_observations: SkinObservations;
  medicine_rag: MedicineRagResult;

  disclaimer: string;
  processing_time_seconds?: number;
  voice_grant?: SkinVoiceGrant;
};

type CaptureResponse = {
  success: boolean;
  status: string;
  message?: string;
};

type Mode =
  | "capture"
  | "test"
  | "local-rag"
  | null;


// =====================================================
// ERROR HANDLING
// =====================================================

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    if (error.name === "AbortError") {
      return (
        "The request timed out. Check your " +
        "FastAPI and Ollama terminals."
      );
    }

    return error.message;
  }

  return "Skin Check could not be completed.";
}


async function readApiResponse(
  response: Response
): Promise<any> {
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    let message = `Request failed (${response.status}).`;

    if (typeof data.detail === "string") {
      message = data.detail;
    } else if (Array.isArray(data.detail)) {
      message = data.detail
        .map((item: any) => item.msg || "Invalid request")
        .join("; ");
    }

    throw new Error(message);
  }

  return data;
}


// =====================================================
// LIVEKIT CAMERA CAPTURE
// =====================================================

async function captureCamera(
  room: Room
): Promise<Blob> {
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
      "Please enable your camera before using Skin Check."
    );
  }

  const stream = new MediaStream([mediaTrack]);

  const video = document.createElement("video");

  video.muted = true;
  video.playsInline = true;
  video.autoplay = true;
  video.srcObject = stream;

  let timer: number | undefined;

  try {
    await Promise.race([
      new Promise<void>((resolve, reject) => {
        const ready = () => {
          if (
            video.videoWidth > 0 &&
            video.videoHeight > 0
          ) {
            resolve();
          }
        };

        video.addEventListener("loadeddata", ready);
        video.addEventListener("playing", ready);

        video.play().then(ready).catch(reject);
      }),

      new Promise<never>((_, reject) => {
        timer = window.setTimeout(() => {
          reject(
            new Error(
              "Camera capture timed out. Please try again."
            )
          );
        }, CAMERA_TIMEOUT);
      }),
    ]);

    // Wait for a few fresh frames.
    await new Promise<void>((resolve) => {
      let frames = 0;

      const next = () => {
        frames++;

        if (frames >= 3) {
          resolve();
        } else {
          requestAnimationFrame(next);
        }
      };

      requestAnimationFrame(next);
    });

    const width = video.videoWidth;
    const height = video.videoHeight;

    if (!width || !height) {
      throw new Error(
        "No video frame is available."
      );
    }

    const maxSide = 1280;

    const scale = Math.min(
      1,
      maxSide / Math.max(width, height)
    );

    const canvas = document.createElement("canvas");

    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);

    const context = canvas.getContext("2d");

    if (!context) {
      throw new Error(
        "Unable to prepare camera snapshot."
      );
    }

    context.drawImage(
      video,
      0,
      0,
      canvas.width,
      canvas.height
    );

    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        (blob) => {
          if (!blob) {
            reject(
              new Error(
                "Unable to encode camera image."
              )
            );
            return;
          }

          resolve(blob);
        },
        "image/jpeg",
        0.85
      );
    });

  } finally {
    if (timer !== undefined) {
      window.clearTimeout(timer);
    }

    video.pause();
    video.srcObject = null;

    // Do not stop the original LiveKit camera track.
  }
}


// =====================================================
// SKIN ANALYSIS COMPONENT
// =====================================================

export default function SkinAnalysis({
  meetingId,
  accessToken,
}: Props) {
  const room = useRoomContext();

  // Camera consent.
  const [consent, setConsent] = useState(false);

  // Explicit consent for processing the snapshot.
  const [localConsent, setLocalConsent] =
    useState(false);


  const [loading, setLoading] = useState(false);

  const [currentMode, setCurrentMode] =
    useState<Mode>(null);

  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const [localResult, setLocalResult] =
    useState<LocalRagResponse | null>(null);

  const [capturedImage, setCapturedImage] =
    useState<Blob | null>(null);

  const [previewUrl, setPreviewUrl] =
    useState<string | null>(null);

  const [voicePlayback, setVoicePlayback] = useState<VoicePlayback>("idle");
  const voiceRequest = useRef<VoiceRequest | null>(null);

  const controllerRef = useRef<AbortController | null>(
    null
  );

  const inFlightRef = useRef(false);

  // ===================================================
  // CLEAN UP
  // ===================================================

  useEffect(() => {
    return () => {
      controllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    return () => {
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
      }
    };
  }, [previewUrl]);

  function clearVoiceRequest() {
    window.clearTimeout(voiceRequest.current?.timer);
    voiceRequest.current = null;
  }

  useEffect(() => {
    function onVoiceStatus(
      payload: Uint8Array,
      sender?: RemoteParticipant,
      _kind?: unknown,
      topic?: string,
    ) {
      const request = voiceRequest.current;
      if (!request || topic !== `${VOICE_TOPIC}-status` || sender?.identity !== request.agentIdentity) {
        return;
      }

      try {
        const reply = JSON.parse(new TextDecoder().decode(payload));
        if (reply.request_id !== request.id) return;

        if (reply.status === "accepted") {
          setVoicePlayback("accepted");
          window.clearTimeout(request.timer);
          request.timer = window.setTimeout(() => {
            clearVoiceRequest();
            setVoicePlayback("idle");
            setError("Mateo hasn't confirmed playback. You can retry.");
          }, 180000);
        } else if (reply.status === "played") {
          clearVoiceRequest();
          setVoicePlayback("played");
        } else if (reply.status === "failed") {
          clearVoiceRequest();
          setVoicePlayback("idle");
          setError(voiceErrors[reply.reason] || voiceErrors.unavailable);
        }
      } catch {
        // Ignore malformed status packets.
      }
    }

    room.on(RoomEvent.DataReceived, onVoiceStatus);
    return () => {
      room.off(RoomEvent.DataReceived, onVoiceStatus);
      clearVoiceRequest();
    };
  }, [room]);


  // ===================================================
  // CLEAR RESULTS
  // ===================================================

  function clearResults() {
    setLocalResult(null);
    clearVoiceRequest();
    setVoicePlayback("idle");
  }


  // ===================================================
  // CLICK PHOTO
  // ===================================================

  async function clickPhoto() {
    if (inFlightRef.current) {
      return;
    }

    if (!consent) {
      setError(
        "Please provide camera capture consent first."
      );
      return;
    }

    inFlightRef.current = true;

    setCurrentMode("capture");
    setLoading(true);
    setError("");
    setMessage("");

    try {
      const blob = await captureCamera(room);

      const url = URL.createObjectURL(blob);

      setCapturedImage(blob);
      setPreviewUrl(url);

      clearResults();

      setMessage(
        "Photo captured successfully. " +
        "You can now analyze it with Local Ollama."
      );

    } catch (err) {
      setError(errorMessage(err));

    } finally {
      setLoading(false);
      setCurrentMode(null);
      inFlightRef.current = false;
    }
  }


  // ===================================================
  // CAMERA TEST
  // ===================================================

  async function testCameraCapture() {
    if (inFlightRef.current) {
      return;
    }

    if (!consent) {
      setError(
        "Please provide camera capture consent first."
      );
      return;
    }

    inFlightRef.current = true;

    setLoading(true);
    setCurrentMode("test");
    setError("");
    setMessage("");

    const controller = new AbortController();
    controllerRef.current = controller;

    let timer: number | undefined;

    try {
      const image =
        capturedImage || await captureCamera(room);

      const form = new FormData();

      form.append("meeting_id", meetingId);
      form.append("consent", "true");
      form.append("image", image, "camera.jpg");

      timer = window.setTimeout(
        () => controller.abort(),
        CAMERA_REQUEST_TIMEOUT
      );

      const response = await fetch(
        `${meetingApiUrl}/api/v1/skin/analyze`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${accessToken}`,
          },
          body: form,
          signal: controller.signal,
        }
      );

      const result =
        await readApiResponse(response) as CaptureResponse;

      setMessage(
        result.message ||
        "Camera capture verified successfully."
      );

    } catch (err) {
      setError(errorMessage(err));

    } finally {
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }

      controllerRef.current = null;
      inFlightRef.current = false;

      setLoading(false);
      setCurrentMode(null);
    }
  }


  // ===================================================
  // LOCAL OLLAMA + MEDICINE RAG
  // ===================================================

  async function analyzeLocalRag() {
    if (inFlightRef.current) {
      return;
    }

    if (!consent || !localConsent) {
      setError(
        "Please enable camera and local RAG consent."
      );
      return;
    }


    inFlightRef.current = true;

    setLoading(true);
    setCurrentMode("local-rag");

    setError("");
    setMessage("");
    clearResults();

    const controller = new AbortController();
    controllerRef.current = controller;

    let timer: number | undefined;

    try {
      const image =
        capturedImage || await captureCamera(room);

      const form = new FormData();

      form.append("meeting_id", meetingId);
      form.append("consent", "true");
      form.append("image", image, "skin-check.jpg");
      form.append("processing_consent", "true");

      timer = window.setTimeout(
        () => controller.abort(),
        LOCAL_RAG_TIMEOUT
      );

      const response = await fetch(
        `${meetingApiUrl}/api/v1/skin/local-rag`,
        {
          method: "POST",

          headers: {
            Authorization: `Bearer ${accessToken}`,
          },

          body: form,
          signal: controller.signal,
        }
      );

      const result =
        await readApiResponse(response) as LocalRagResponse;

      if (!result.success) {
        throw new Error(
          "Local Ollama analysis did not succeed."
        );
      }

      setLocalResult(result);

      setMessage(
        "Local Ollama + Medicine RAG analysis completed."
      );

    } catch (err) {
      setError(errorMessage(err));

    } finally {
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }

      controllerRef.current = null;
      inFlightRef.current = false;

      setLoading(false);
      setCurrentMode(null);
    }
  }


  // ===================================================
  // SPEAK RESULTS WITH MATEO
  // ===================================================

  async function speakSkinResults() {
    const grant = localResult?.voice_grant;

    if (!grant) {
      setError(
        "Voice authorization missing. " +
        "Run Local Ollama analysis again."
      );
      return;
    }

    if (
      Date.now() / 1000 >= grant.payload.expires_at
    ) {
      setError(
        "Voice authorization expired. " +
        "Run Skin Analysis again."
      );
      return;
    }

    if (room.state !== ConnectionState.Connected) {
      setError(
        "Please connect to the Mateo meeting first."
      );
      return;
    }

    if (voiceRequest.current || voicePlayback === "played") return;

    const agent = Array.from(room.remoteParticipants.values()).find(
      (participant) => participant.kind === ParticipantKind.AGENT &&
        participant.attributes["mateo.skin_voice"] === "ready",
    );
    if (!agent) {
      setError("Mateo isn't ready to play Skin Check results yet.");
      return;
    }

    const confirmed = window.confirm(
      "Mateo will speak a short summary of " +
      "your skin observations aloud. " +
      "EVERYONE in the meeting may hear it. " +
      "Do you want to continue?"
    );

    if (!confirmed) {
      return;
    }

    void room.startAudio().catch(() => {
      // The meeting's Enable meeting audio button remains available.
    });
    const request: VoiceRequest = { id: crypto.randomUUID(), agentIdentity: agent.identity };
    voiceRequest.current = request;
    setVoicePlayback("requesting");
    setError("");
    setMessage("");
    request.timer = window.setTimeout(() => {
      clearVoiceRequest();
      setVoicePlayback("idle");
      setError("Mateo didn't acknowledge the request. You can retry.");
    }, 12000);

    try {
      const encoded = new TextEncoder().encode(
        JSON.stringify({
          request_id: request.id,
          voice_grant: grant,
        })
      );

      await room.localParticipant.publishData(
        encoded,
        {
          reliable: true,
          topic: VOICE_TOPIC,
          destinationIdentities: [agent.identity],
        }
      );
    } catch (err) {
      if (voiceRequest.current === request) {
        clearVoiceRequest();
        setVoicePlayback("idle");
        setError(errorMessage(err));
      }
    }
  }


  // ===================================================
  // UI
  // ===================================================

  return (
    <div className="skin-check">

      <h3>Skin Check — Private</h3>

      <p>
        Capture one frame from your own LiveKit camera
        and analyze it using your local Ollama model.
        Other meeting participants are not scanned
        automatically.
      </p>

      <p>
        Experimental visual observations only.
        Not a medical diagnosis.
      </p>

      {/* =============================================
          CAMERA CONSENT
      ============================================= */}

      <label className="skin-consent">
        <input
          type="checkbox"
          checked={consent}
          disabled={loading}
          onChange={(event) =>
            setConsent(event.target.checked)
          }
        />

        I consent to capturing and submitting one
        image from my camera for this Skin Check.
      </label>


      {/* =============================================
          LOCAL RAG CONSENT
      ============================================= */}

      <label className="skin-consent">
        <input
          type="checkbox"
          checked={localConsent}
          disabled={loading}
          onChange={(event) =>
            setLocalConsent(event.target.checked)
          }
        />

        I consent to AI processing of this snapshot on the server.
        The results are experimental observations using unreviewed
        reference documents, not a medical diagnosis.
      </label>




      {/* =============================================
          PHOTO + RAG UPLOAD BUTTONS
      ============================================= */}

      <div
        className="skin-actions"
        style={{
          display: "flex",
          gap: 12,
          alignItems: "flex-start",
          flexWrap: "wrap",
          marginTop: 16,
        }}
      >

        <RagDocumentUpload
          accessToken={accessToken}
          meetingId={meetingId}
          disabled={loading}
          onUploaded={clearResults}
        />

        <button
          type="button"
          disabled={loading || !consent}
          onClick={() => void clickPhoto()}
        >
          {loading && currentMode === "capture"
            ? "Capturing..."
            : "Click Photo"}
        </button>


      </div>

      <p>
        Upload a TXT, MD, or text-based PDF (up to 10 MB). Your documents are
        available only to your participant in this meeting for 24 hours.
        Skin Check uses matching passages from your files. To ask about an upload,
        click Talk to Mateo and say “What does my uploaded document say about redness?”
        Everyone in the meeting can hear the spoken answer.
      </p>


      {/* =============================================
          PHOTO PREVIEW
      ============================================= */}

      {previewUrl && (
        <div
          style={{
            marginTop: 16,
            maxWidth: 400,
          }}
        >

          <h4>Captured Photo</h4>

          <img
            src={previewUrl}
            alt="Your captured camera snapshot"
            style={{
              width: "100%",
              borderRadius: 12,
              border: "1px solid #334155",
            }}
          />

          <p style={{ fontSize: 13 }}>
            This snapshot will be used for your next
            Skin Analysis. Click Photo again to retake it.
          </p>

        </div>
      )}


      {/* =============================================
          LOCAL ANALYSIS BUTTONS
      ============================================= */}

      <div
        className="skin-actions"
        style={{
          display: "flex",
          gap: 12,
          flexWrap: "wrap",
          marginTop: 18,
        }}
      >

        <button
          type="button"
          disabled={loading || !consent}
          onClick={() => void testCameraCapture()}
        >
          {loading && currentMode === "test"
            ? "Testing Camera..."
            : "Test Camera Capture"}
        </button>

        <button
          type="button"
          disabled={
            loading ||
            !consent ||
            !localConsent
          }
          onClick={() => void analyzeLocalRag()}
        >
          {loading && currentMode === "local-rag"
            ? "Analyzing with Local Ollama..."
            : "Analyze with Local Ollama + Medicine RAG"}
        </button>

      </div>


      {/* =============================================
          LOADING STATUS
      ============================================= */}

      {loading && (
        <p
          role="status"
          style={{
            marginTop: 14,
            color: "#a9c7ef",
          }}
        >
          {currentMode === "local-rag"
            ? "Running local Qwen3-VL inference and Medicine RAG retrieval. CPU analysis may take a few minutes."
            : currentMode === "capture"
              ? "Capturing a fresh camera frame..."
              : "Verifying the camera snapshot..."}
        </p>
      )}


      {/* =============================================
          SUCCESS MESSAGE
      ============================================= */}

      {message && (
        <p
          role="status"
          className="skin-success"
        >
          {message}
        </p>
      )}


      {/* =============================================
          ERROR MESSAGE
      ============================================= */}

      {error && (
        <p
          role="alert"
          className="skin-error"
        >
          {error}
        </p>
      )}


      {/* =============================================
          LOCAL OLLAMA RESULTS
      ============================================= */}

      {localResult && (
        <div className="skin-results">

          <h4>
            Local Vision Observations
            (Not a Diagnosis)
          </h4>

          {/* =======================================
              SPEAK RESULTS BUTTON
          ======================================= */}

          {localResult.voice_grant && (
            <div
              style={{
                margin: "14px 0",
              }}
            >

              <button
                type="button"
                disabled={loading || voicePlayback !== "idle"}
                onClick={() => void speakSkinResults()}
              >
                Speak Results to Meeting
              </button>

              {voicePlayback !== "idle" && (
                <p role="status">
                  {voicePlayback === "requesting" ? "Requesting playback…" :
                    voicePlayback === "accepted" ? "Mateo will read the summary aloud." :
                    "Mateo finished reading the summary."}
                </p>
              )}

              <p
                style={{
                  fontSize: 13,
                  color: "#fbbf24",
                }}
              >
                Everyone in the meeting may hear
                Mateo's spoken summary.
              </p>

            </div>
          )}


          <p>
            <strong>Model:</strong>{" "}
            {localResult.skin_observations.model ||
              "qwen3-vl:2b-instruct"}
          </p>

          <p>
            <strong>Image quality:</strong>{" "}
            {localResult.skin_observations.quality_note ||
              "Not provided"}
          </p>

          <p>
            <strong>Face visible:</strong>{" "}
            {String(
              localResult.skin_observations.face_visible
            )}
          </p>

          <p>
            <strong>Image usable:</strong>{" "}
            {String(
              localResult.skin_observations.image_usable
            )}
          </p>

          <p>
            <strong>Visible features:</strong>{" "}
            {localResult.skin_observations.visible_features
              ?.length
              ? localResult.skin_observations
                  .visible_features.join(", ")
              : "None reported"}
          </p>

          {localResult.skin_observations
            .processed_image_size && (

            <p>
              <strong>Processed image:</strong>{" "}
              {
                localResult.skin_observations
                  .processed_image_size.width
              }
              {" × "}
              {
                localResult.skin_observations
                  .processed_image_size.height
              }
            </p>
          )}

          {localResult.processing_time_seconds !==
            undefined && (

            <p>
              <strong>Processing time:</strong>{" "}
              {localResult.processing_time_seconds}
              {" seconds"}
            </p>
          )}


          {/* =======================================
              MEDICINE RAG REFERENCES
          ======================================= */}

          <h4 style={{ marginTop: 24 }}>
            Development Medicine RAG References
          </h4>

          <p>
            Review status:{" "}
            <strong>
              {localResult.medicine_rag.status}
            </strong>
            {" — not medically approved."}
          </p>

          {localResult.medicine_rag.results.length === 0 ? (

            <p>
              No matching Medicine RAG references
              were retrieved.
            </p>

          ) : (

            <ul>
              {localResult.medicine_rag.results.map(
                (reference, index) => (

                  <li
                    key={`${reference.source}-${index}`}
                    style={{
                      marginBottom: 18,
                    }}
                  >

                    <strong>
                      {reference.source}
                    </strong>

                    {reference.page !== null &&
                      reference.page !== undefined && (
                        <span>
                          {" — Page "}
                          {reference.page}
                        </span>
                    )}

                    {" ("}
                    {reference.review_status}
                    {")"}

                    <p
                      style={{
                        whiteSpace: "pre-wrap",
                        overflowWrap: "anywhere",
                      }}
                    >
                      {reference.excerpt}
                    </p>

                  </li>
                )
              )}
            </ul>

          )}


          <p
            style={{
              marginTop: 20,
              fontSize: 13,
              opacity: 0.9,
            }}
          >
            {localResult.disclaimer ||
              "Experimental visual observations only. Not a medical diagnosis. References are unreviewed development materials, not treatment recommendations."}
          </p>

        </div>
      )}

    </div>
  );
}
