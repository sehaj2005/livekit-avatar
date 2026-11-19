
import React, {
  useEffect,
  useRef,
  useState,
} from 'react';

import {
  useRoomContext,
} from '@livekit/components-react';

import {
  Room,
  Track,
} from 'livekit-client';

import './skin-analysis.css';


const API =
  import.meta.env.VITE_API_URL ||
  'http://127.0.0.1:8000';

const REQUEST_TIMEOUT = 90000;
const CAMERA_TIMEOUT = 8000;


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


type Props = {
  meetingId: string;
  accessToken: string;
};


function errorMessage(error: unknown): string {
  if (
    error instanceof Error &&
    error.name === 'AbortError'
  ) {
    return (
      'The request timed out. ' +
      'Gemini may be unavailable. Please retry.'
    );
  }

  if (error instanceof Error) {
    return error.message;
  }

  return 'Skin Check could not be completed.';
}


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
    mediaTrack.readyState !== 'live'
  ) {
    throw new Error(
      'Please turn on your camera before using Skin Check.'
    );
  }

  const stream = new MediaStream([
    mediaTrack,
  ]);

  const video = document.createElement(
    'video'
  );

  video.muted = true;
  video.playsInline = true;
  video.autoplay = true;
  video.srcObject = stream;

  let timeoutId: number | undefined;

  try {
    await Promise.race([
      new Promise<void>(
        (resolve, reject) => {
          const onReady = () => {
            if (
              video.videoWidth > 0 &&
              video.videoHeight > 0
            ) {
              resolve();
            }
          };

          video.addEventListener(
            'loadeddata',
            onReady
          );

          video.addEventListener(
            'playing',
            onReady
          );

          video.play().then(
            onReady
          ).catch(reject);
        }
      ),

      new Promise<never>(
        (_, reject) => {
          timeoutId = window.setTimeout(
            () => {
              reject(
                new Error(
                  'Camera capture timed out. Please try again.'
                )
              );
            },
            CAMERA_TIMEOUT
          );
        }
      ),
    ]);

    const width = video.videoWidth;
    const height = video.videoHeight;

    if (!width || !height) {
      throw new Error(
        'The camera has no available video frame.'
      );
    }

    const maxSide = 1280;

    const scale = Math.min(
      1,
      maxSide / Math.max(
        width,
        height
      )
    );

    const canvas =
      document.createElement('canvas');

    canvas.width = Math.round(
      width * scale
    );

    canvas.height = Math.round(
      height * scale
    );

    const context =
      canvas.getContext('2d');

    if (!context) {
      throw new Error(
        'Could not prepare camera snapshot.'
      );
    }

    context.drawImage(
      video,
      0,
      0,
      canvas.width,
      canvas.height
    );

    const blob = await new Promise<Blob>(
      (resolve, reject) => {
        canvas.toBlob(
          (result) => {
            if (!result) {
              reject(
                new Error(
                  'Could not encode camera image.'
                )
              );
              return;
            }

            resolve(result);
          },
          'image/jpeg',
          0.85
        );
      }
    );

    return blob;

  } finally {
    if (timeoutId !== undefined) {
      window.clearTimeout(timeoutId);
    }

    video.pause();
    video.srcObject = null;
  }
}


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

  const [loading, setLoading] =
    useState(false);

  const [currentMode, setCurrentMode] =
    useState<'analyze' | 'vision' | null>(
      null
    );

  const [message, setMessage] =
    useState('');

  const [error, setError] =
    useState('');

  const [result, setResult] =
    useState<VisionResponse | null>(
      null
    );

  const controllerRef =
    useRef<AbortController | null>(null);

  const inFlightRef =
    useRef(false);


  useEffect(() => {
    setResult(null);
    setMessage('');
    setError('');

    return () => {
      controllerRef.current?.abort();
    };
  }, [meetingId]);


  async function submit(
    mode: 'analyze' | 'vision'
  ) {
    if (inFlightRef.current) {
      return;
    }

    if (!consent) {
      setError(
        'Please consent to capturing your camera image.'
      );
      return;
    }

    if (
      mode === 'vision' &&
      !externalConsent
    ) {
      setError(
        'Please consent to Gemini processing before AI analysis.'
      );
      return;
    }

    inFlightRef.current = true;

    setLoading(true);
    setCurrentMode(mode);
    setError('');
    setMessage('');
    setResult(null);

    const controller =
      new AbortController();

    controllerRef.current = controller;

    let timeoutId: number | undefined;

    try {
      const blob = await captureCamera(
        room
      );

      if (controller.signal.aborted) {
        return;
      }

      const formData = new FormData();

      formData.append(
        'meeting_id',
        meetingId
      );

      formData.append(
        'consent',
        'true'
      );

      if (mode === 'vision') {
        formData.append(
          'external_processing_consent',
          'true'
        );
      }

      formData.append(
        'image',
        blob,
        'camera-snapshot.jpg'
      );

      timeoutId = window.setTimeout(
        () => {
          controller.abort();
        },
        REQUEST_TIMEOUT
      );

      const response = await fetch(
        `${API}/api/v1/skin/${mode}`,
        {
          method: 'POST',

          headers: {
            Authorization:
              `Bearer ${accessToken}`,
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
          `The backend returned an invalid response (${response.status}).`
        );
      }

      const data = payload as {
        detail?: string | unknown[];
        message?: string;
      };

      if (!response.ok) {
        let description =
          `Skin Check failed (${response.status}).`;

        if (
          typeof data.detail === 'string'
        ) {
          description = data.detail;
        }

        if (response.status === 503) {
          description =
            'Gemini is temporarily unavailable. Please try again later.';
        }

        if (response.status === 504) {
          description =
            'Skin analysis timed out. Please retry.';
        }

        if (response.status === 429) {
          description =
            'Gemini rate limit reached. Please retry later.';
        }

        throw new Error(description);
      }

      if (mode === 'vision') {
        const visionData =
          payload as VisionResponse;

        if (!visionData.assessment) {
          throw new Error(
            'The backend did not return a skin assessment.'
          );
        }

        setResult(visionData);

        setMessage(
          'Skin analysis completed successfully.'
        );

      } else {
        const captureData =
          payload as CaptureResponse;

        setMessage(
          captureData.message ||
          'Image capture verified successfully. No image was stored.'
        );
      }

    } catch (err) {
      setError(errorMessage(err));

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


  return (
    <div className="skin-check">

      <h3>
        Skin Check — Private
      </h3>

      <p>
        Analyze{' '}
        <strong>
          one frame from your own camera
        </strong>
        . Other meeting participants
        are never scanned automatically.
        This is experimental visual information,
        not a medical diagnosis.
      </p>


      <label className="skin-consent">
        <input
          type="checkbox"
          checked={consent}
          onChange={(event) =>
            setConsent(event.target.checked)
          }
          disabled={loading}
        />

        I consent to capturing and submitting
        one image from my camera for this check.
      </label>


      <label className="skin-consent">
        <input
          type="checkbox"
          checked={externalConsent}
          onChange={(event) =>
            setExternalConsent(
              event.target.checked
            )
          }
          disabled={loading}
        />

        For AI analysis, I additionally consent
        to sending this face image to Google's
        Gemini service. Its processing and
        retention are subject to the
        provider's terms. Mateo does not
        save the image in its database.
      </label>


      <div className="skin-actions">

        <button
          type="button"
          disabled={loading || !consent}
          onClick={() =>
            void submit('analyze')
          }
        >
          {loading &&
          currentMode === 'analyze'
            ? 'Testing Camera...'
            : 'Test Camera Capture'}
        </button>


        <button
          type="button"
          disabled={
            loading ||
            !consent ||
            !externalConsent
          }
          onClick={() =>
            void submit('vision')
          }
        >
          {loading &&
          currentMode === 'vision'
            ? 'Processing...'
            : 'Analyze Visible Skin Features'}
        </button>

      </div>


      {loading && (
        <p
          role="status"
          style={{
            marginTop: 12,
            color: '#a9c7ef',
          }}
        >
          {currentMode === 'vision'
            ? 'Analyzing your camera snapshot. The request will time out if Gemini does not respond.'
            : 'Verifying your camera snapshot...'}
        </p>
      )}


      {message && (
        <p
          role="status"
          className="skin-success"
        >
          {message}
        </p>
      )}


      {error && (
        <p
          role="alert"
          className="skin-error"
        >
          {error}
        </p>
      )}


      {result && (
        <div className="skin-results">

          <h4>
            Visual Observations
            (Not a Diagnosis)
          </h4>


          <p>
            <strong>
              Image quality:
            </strong>{' '}
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

                      {' — '}

                      {observation.location}
                      {': '}
                      {observation.description}
                    </li>
                  )
                )}
              </ul>

            ) : (
              <p>
                No clearly distinguishable
                skin features were identified.
                This does not rule out
                a skin condition.
              </p>
            )

          ) : (
            <p>
              This image is not suitable
              for a reliable visual description.
              Try better lighting and a clear
              close-up image.
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
            </strong>{' '}
            {result.assessment.referral_note}
          </p>


          <p className="skin-disclaimer">
            {result.disclaimer}
          </p>

          <p className="skin-disclaimer">
            If you experience difficulty
            breathing or swallowing, or
            sudden swelling involving
            your lips or tongue, seek
            emergency medical assistance.
          </p>

        </div>
      )}

    </div>
  );
}
