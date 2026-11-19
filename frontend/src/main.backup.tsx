
import React, {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from 'react';

import { createRoot } from 'react-dom/client';

import {
  LiveKitRoom,
  ParticipantTile,
  RoomAudioRenderer,
  useParticipants,
  useRoomContext,
  useTracks,
} from '@livekit/components-react';

import {
  RoomEvent,
  Track,
} from 'livekit-client';

import '@livekit/components-styles';
import './style.css';

import AdminDashboard from './AdminDashboard';
import SkinAnalysis from './SkinAnalysis';

const API_URL =
  import.meta.env.VITE_API_URL ||
  'http://127.0.0.1:8000';

const LIVEKIT_URL =
  import.meta.env.VITE_LIVEKIT_URL || '';

const API_BASE = `${API_URL}/api/v1`;

type MeetingInfo = {
  meetingId: string;
  inviteCode: string;
  ownerToken: string;
};

type Credentials = {
  access_token: string;
  server_url: string;
  meetingId: string;
  inviteCode: string;
  displayName: string;
};

type CreateMeetingResponse = {
  meeting_id?: string;
  meetingId?: string;
  invite_code?: string;
  inviteCode?: string;
  owner_token?: string;
  ownerToken?: string;
};

type JoinResponse = {
  access_token?: string;
  token?: string;
  livekit_token?: string;
  server_url?: string;
  livekit_url?: string;
  url?: string;
};

const inputStyle: React.CSSProperties = {
  display: 'block',
  width: '100%',
  height: '48px',
  padding: '12px 15px',
  boxSizing: 'border-box',
  background: '#1c2c44',
  border: '1px solid #3b526f',
  borderRadius: '9px',
  color: '#ffffff',
  fontSize: '16px',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  marginBottom: '9px',
  color: '#ffffff',
  fontSize: '15px',
  fontWeight: 600,
};

const buttonStyle: React.CSSProperties = {
  padding: '12px 18px',
  borderRadius: '10px',
  border: '1px solid #344e71',
  background: '#1e3a60',
  color: '#ffffff',
  fontWeight: 600,
  cursor: 'pointer',
};

function getErrorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : 'Something went wrong.';
}

async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers);

  if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(
    `${API_BASE}${endpoint}`,
    {
      ...options,
      headers,
    }
  );

  if (!response.ok) {
    let message = `Request failed (${response.status})`;

    try {
      const body = await response.json();

      if (typeof body.detail === 'string') {
        message = body.detail;
      }
    } catch {
      // Preserve the HTTP error.
    }

    throw new Error(message);
  }

  return (await response.json()) as T;
}

function getMeetingFromUrl() {
  const params = new URLSearchParams(
    window.location.search
  );

  return {
    meetingId:
      params.get('meeting') ||
      params.get('meeting_id') ||
      '',
    inviteCode:
      params.get('invite') ||
      params.get('code') ||
      '',
  };
}

function getInviteLink(
  meetingId: string,
  inviteCode: string
): string {
  const url = new URL(window.location.origin);

  url.searchParams.set('meeting', meetingId);
  url.searchParams.set('invite', inviteCode);

  return url.toString();
}

async function createMeeting(): Promise<MeetingInfo> {
  const result =
    await apiRequest<CreateMeetingResponse>(
      '/meetings',
      {
        method: 'POST',
        body: JSON.stringify({}),
      }
    );

  const meetingId =
    result.meeting_id ||
    result.meetingId ||
    '';

  const inviteCode =
    result.invite_code ||
    result.inviteCode ||
    '';

  const ownerToken =
    result.owner_token ||
    result.ownerToken ||
    '';

  if (!meetingId || !inviteCode || !ownerToken) {
    throw new Error(
      'Backend returned incomplete meeting information.'
    );
  }

  return {
    meetingId,
    inviteCode,
    ownerToken,
  };
}

async function joinMeeting(
  meetingId: string,
  inviteCode: string,
  name: string
): Promise<Credentials> {
  const result = await apiRequest<JoinResponse>(
    `/meetings/${encodeURIComponent(meetingId)}/join`,
    {
      method: 'POST',
      body: JSON.stringify({
        name,
        display_name: name,
        invite_code: inviteCode,
      }),
    }
  );

  const accessToken =
    result.access_token ||
    result.livekit_token ||
    result.token ||
    '';

  const serverUrl =
    result.server_url ||
    result.livekit_url ||
    result.url ||
    LIVEKIT_URL;

  if (!accessToken) {
    throw new Error(
      'Backend did not return a LiveKit token.'
    );
  }

  if (!serverUrl) {
    throw new Error(
      'LiveKit server URL is missing.'
    );
  }

  return {
    access_token: accessToken,
    server_url: serverUrl,
    meetingId,
    inviteCode,
    displayName: name,
  };
}

async function startMateoAgent(
  meetingId: string,
  ownerToken: string
): Promise<void> {
  const response = await fetch(
    `${API_BASE}/meetings/${encodeURIComponent(meetingId)}/agent/start`,
    {
      method: 'POST',
      headers: {
        'X-Owner-Token': ownerToken,
      },
    }
  );

  if (!response.ok) {
    let message =
      `Could not start Mateo (${response.status}).`;

    try {
      const body = await response.json();

      if (typeof body.detail === 'string') {
        message = body.detail;
      }
    } catch {
      // Preserve the HTTP error.
    }

    throw new Error(message);
  }
}

function ParticipantVideoGrid() {
  const participants = useParticipants();

  const tracks = useTracks(
    [
      Track.Source.Camera,
      Track.Source.ScreenShare,
    ],
    {
      onlySubscribed: false,
    }
  );

  const visibleParticipants = useMemo(
    () =>
      participants.filter(
        (participant) =>
          participant.isLocal ||
          participant.identity.startsWith('human_') ||
          participant.identity.startsWith('simli-')
      ),
    [participants]
  );

  const visibleTracks = useMemo(
    () =>
      tracks.filter(
        (track) =>
          track.participant.isLocal ||
          track.participant.identity.startsWith('human_') ||
          track.participant.identity.startsWith('simli-')
      ),
    [tracks]
  );

  const participantsWithVideo = new Set(
    visibleTracks.map(
      (track) => track.participant.identity
    )
  );

  return (
    <div className="video-grid">
      {visibleTracks.map((track) => (
        <div
          className="video-tile"
          key={`${track.participant.identity}-${track.source}`}
        >
          <ParticipantTile trackRef={track} />
        </div>
      ))}

      {visibleParticipants
        .filter(
          (participant) =>
            !participantsWithVideo.has(
              participant.identity
            )
        )
        .map((participant) => (
          <div
            className="video-tile"
            key={participant.identity}
          >
            <div
              style={{
                display: 'flex',
                minHeight: 280,
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 12,
                background: '#18263c',
                borderRadius: 12,
              }}
            >
              <span style={{ fontSize: 44 }}>
                👤
              </span>

              <strong>
                {participant.name ||
                  participant.identity}
              </strong>

              <span>Camera off</span>
            </div>
          </div>
        ))}
    </div>
  );
}

function GroupMeeting({
  meetingId,
  inviteCode,
  accessToken,
  onLeave,
}: {
  meetingId: string;
  inviteCode: string;
  accessToken: string;
  onLeave: () => void;
}) {
  const room = useRoomContext();
  const participants = useParticipants();

  const [skinPanelOpen, setSkinPanelOpen] =
    useState(false);

  const [microphoneEnabled, setMicrophoneEnabled] =
    useState(true);

  const [cameraEnabled, setCameraEnabled] =
    useState(true);

  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [copied, setCopied] = useState(false);
  const [selectedSpeaker, setSelectedSpeaker] =
    useState('');

  const inviteLink = useMemo(
    () => getInviteLink(meetingId, inviteCode),
    [meetingId, inviteCode]
  );

  const humanParticipants = useMemo(
    () =>
      participants.filter(
        (participant) =>
          participant.isLocal ||
          participant.identity.startsWith('human_')
      ),
    [participants]
  );

  useEffect(() => {
    function syncDevices() {
      setMicrophoneEnabled(
        room.localParticipant.isMicrophoneEnabled
      );

      setCameraEnabled(
        room.localParticipant.isCameraEnabled
      );
    }

    syncDevices();

    room.on(
      RoomEvent.LocalTrackPublished,
      syncDevices
    );

    room.on(
      RoomEvent.LocalTrackUnpublished,
      syncDevices
    );

    room.on(RoomEvent.TrackMuted, syncDevices);
    room.on(RoomEvent.TrackUnmuted, syncDevices);

    return () => {
      room.off(
        RoomEvent.LocalTrackPublished,
        syncDevices
      );

      room.off(
        RoomEvent.LocalTrackUnpublished,
        syncDevices
      );

      room.off(RoomEvent.TrackMuted, syncDevices);
      room.off(RoomEvent.TrackUnmuted, syncDevices);
    };
  }, [room]);

  useEffect(() => {
    const handleData = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      if (topic && topic !== 'mateo-floor') {
        return;
      }

      try {
        const data = JSON.parse(
          new TextDecoder().decode(payload)
        );

        if (data.type !== 'mateo-floor') {
          return;
        }

        const identity =
          data.participant_identity ||
          data.identity ||
          data.speaker ||
          '';

        if (typeof identity === 'string') {
          setSelectedSpeaker(identity);
        }
      } catch {
        // Ignore unrelated room messages.
      }
    };

    room.on(RoomEvent.DataReceived, handleData);

    return () => {
      room.off(RoomEvent.DataReceived, handleData);
    };
  }, [room]);

  async function copyInviteLink() {
    try {
      await navigator.clipboard.writeText(
        inviteLink
      );

      setCopied(true);
    } catch (error) {
      setMessage(getErrorMessage(error));
    }
  }

  async function talkToMateo() {
    setBusy(true);
    setMessage('');

    try {
      const identity =
        room.localParticipant.identity;

      const payload = new TextEncoder().encode(
        JSON.stringify({
          type: 'mateo-floor',
          action: 'claim',
          identity,
          participant_identity: identity,
        })
      );

      await room.localParticipant.publishData(
        payload,
        {
          reliable: true,
          topic: 'mateo-floor',
        }
      );

      setSelectedSpeaker(identity);
      setMessage(
        'You requested to speak with Mateo.'
      );
    } catch (error) {
      setMessage(getErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function toggleMicrophone() {
    setBusy(true);

    try {
      const next = !microphoneEnabled;

      await room.localParticipant.setMicrophoneEnabled(
        next
      );

      setMicrophoneEnabled(next);
    } catch (error) {
      setMessage(getErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function toggleCamera() {
    setBusy(true);

    try {
      const next = !cameraEnabled;

      await room.localParticipant.setCameraEnabled(
        next
      );

      setCameraEnabled(next);
    } catch (error) {
      setMessage(getErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function leaveRoom() {
    setBusy(true);

    try {
      await room.disconnect();
    } finally {
      onLeave();
    }
  }

  const speaker = humanParticipants.find(
    (participant) =>
      participant.identity === selectedSpeaker
  );

  return (
    <main className="meeting group-meeting">
      <div
        className="meeting-toolbar"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: 16,
          padding: '20px 24px',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            flexWrap: 'wrap',
          }}
        >
          <h2 style={{ margin: 0 }}>
            Mateo Group Meeting
          </h2>

          <span>
            {humanParticipants.length} human(s)
          </span>
        </div>

        <div
          className="meeting-controls"
          style={{
            display: 'flex',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: 10,
          }}
        >
          <button
            type="button"
            style={buttonStyle}
            onClick={() => void copyInviteLink()}
          >
            {copied
              ? 'Link Copied!'
              : 'Copy Invite Link'}
          </button>

          <button
            type="button"
            style={{
              ...buttonStyle,
              background: '#2563eb',
            }}
            onClick={() => void talkToMateo()}
            disabled={busy}
          >
            🎤 Talk to Mateo
          </button>

          <button
            type="button"
            style={{
              ...buttonStyle,
              background: '#087f8c',
              borderColor: '#11b5b2',
            }}
            onClick={() =>
              setSkinPanelOpen(
                (previous) => !previous
              )
            }
          >
            {skinPanelOpen
              ? '✕ Close Skin Check'
              : '🔍 Skin Check (Private)'}
          </button>

          <button
            type="button"
            style={buttonStyle}
            onClick={() => void toggleMicrophone()}
            disabled={busy}
          >
            {microphoneEnabled
              ? 'Mute Mic'
              : 'Unmute Mic'}
          </button>

          <button
            type="button"
            style={buttonStyle}
            onClick={() => void toggleCamera()}
            disabled={busy}
          >
            {cameraEnabled
              ? 'Camera Off'
              : 'Camera On'}
          </button>

          <button
            type="button"
            style={{
              ...buttonStyle,
              background: '#7f1d1d',
            }}
            onClick={() => void leaveRoom()}
            disabled={busy}
          >
            Leave Room
          </button>
        </div>

        <div className="note">
          Mateo listening to:{' '}
          {speaker?.name ||
            speaker?.identity ||
            'Not selected'}
        </div>
      </div>

      {message && (
        <p className="note" role="status">
          {message}
        </p>
      )}

      {skinPanelOpen && (
        <section
          style={{
            margin: '16px 24px',
            padding: 20,
            background: '#132238',
            border: '1px solid #245c6b',
            borderRadius: 14,
          }}
        >
          <SkinAnalysis
            meetingId={meetingId}
            accessToken={accessToken}
          />
        </section>
      )}

      <ParticipantVideoGrid />

      <RoomAudioRenderer />
    </main>
  );
}

function App() {
  const meetingFromUrl = useMemo(
    () => getMeetingFromUrl(),
    []
  );

  const [name, setName] = useState('');

  const [joinExisting, setJoinExisting] =
    useState(
      Boolean(meetingFromUrl.meetingId)
    );

  const [meetingId, setMeetingId] =
    useState(meetingFromUrl.meetingId);

  const [inviteCode, setInviteCode] =
    useState(meetingFromUrl.inviteCode);

  const [credentials, setCredentials] =
    useState<Credentials | null>(null);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const handleLeave = useCallback(() => {
    setCredentials(null);
  }, []);

  async function createNewMeeting() {
    if (!name.trim()) {
      setError('Please enter your name.');
      return;
    }

    setLoading(true);
    setError('');
    setNotice('');

    try {
      const meeting = await createMeeting();

      setMeetingId(meeting.meetingId);
      setInviteCode(meeting.inviteCode);

      const joined = await joinMeeting(
        meeting.meetingId,
        meeting.inviteCode,
        name.trim()
      );

      setCredentials(joined);

      try {
        await startMateoAgent(
          meeting.meetingId,
          meeting.ownerToken
        );
      } catch (agentError) {
        setNotice(
          `Mateo agent could not start: ${getErrorMessage(agentError)}`
        );
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function joinExistingMeeting() {
    if (!name.trim()) {
      setError('Please enter your name.');
      return;
    }

    if (!meetingId.trim()) {
      setError('Please enter the Meeting ID.');
      return;
    }

    if (!inviteCode.trim()) {
      setError('Please enter the Invite Code.');
      return;
    }

    setLoading(true);
    setError('');
    setNotice('');

    try {
      const joined = await joinMeeting(
        meetingId.trim(),
        inviteCode.trim(),
        name.trim()
      );

      setCredentials(joined);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  if (credentials) {
    return (
      <div className="app">
        <header
          className="meeting-header"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '22px 30px',
          }}
        >
          <h1 style={{ margin: 0 }}>
            Mateo AI — Group Meetings
          </h1>

          <div className="meeting-header-actions">
            <span>LiveKit + Simli</span>
            <a href="/admin">Super Admin</a>
          </div>
        </header>

        <LiveKitRoom
          serverUrl={credentials.server_url}
          token={credentials.access_token}
          connect={true}
          audio={true}
          video={true}
          onDisconnected={handleLeave}
          onError={(err) =>
            setError(getErrorMessage(err))
          }
        >
          <GroupMeeting
            meetingId={credentials.meetingId}
            inviteCode={credentials.inviteCode}
            accessToken={credentials.access_token}
            onLeave={handleLeave}
          />
        </LiveKitRoom>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <div
      className="app"
      style={{
        minHeight: '100vh',
        background: '#0b1120',
      }}
    >
      <header
        className="meeting-header"
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 12,
          padding: '22px 30px',
          background: '#0b1424',
        }}
      >
        <h1
          style={{
            color: '#ffffff',
            margin: 0,
            fontSize: '26px',
          }}
        >
          Mateo AI — Group Meetings
        </h1>

        <div
          className="meeting-header-actions"
          style={{
            display: 'flex',
            gap: 16,
            alignItems: 'center',
          }}
        >
          <span
            style={{ color: '#9cb4d7' }}
          >
            LiveKit + Simli
          </span>

          <a
            href="/admin"
            style={{
              color: '#93b7ff',
              textDecoration: 'none',
            }}
          >
            Super Admin Dashboard
          </a>
        </div>
      </header>

      <main
        style={{
          minHeight: 'calc(100vh - 90px)',
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'center',
          padding: '55px 20px',
          background: '#0b1120',
          boxSizing: 'border-box',
        }}
      >
        <div
          style={{
            width: '100%',
            maxWidth: 500,
            padding: 36,
            boxSizing: 'border-box',
            background: '#111d31',
            border: '1px solid #293c59',
            borderRadius: 18,
            boxShadow:
              '0 15px 40px rgba(0,0,0,0.2)',
          }}
        >
          <h2
            style={{
              color: '#ffffff',
              fontSize: 28,
              margin: '0 0 10px',
            }}
          >
            Welcome to Mateo AI
          </h2>

          <p
            style={{
              color: '#a5b4cc',
              margin: '0 0 30px',
            }}
          >
            Your real-time AI meeting assistant
          </p>

          <form
            onSubmit={(event) => {
              event.preventDefault();

              if (joinExisting) {
                void joinExistingMeeting();
              } else {
                void createNewMeeting();
              }
            }}
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 18,
            }}
          >
            <div>
              <label
                htmlFor="mateo-name"
                style={labelStyle}
              >
                Your Name
              </label>

              <input
                id="mateo-name"
                type="text"
                placeholder="Enter your name"
                value={name}
                onChange={(event) =>
                  setName(event.target.value)
                }
                autoComplete="name"
                maxLength={80}
                required
                style={inputStyle}
              />
            </div>

            {joinExisting && (
              <>
                <div>
                  <label
                    htmlFor="mateo-meeting-id"
                    style={labelStyle}
                  >
                    Meeting ID
                  </label>

                  <input
                    id="mateo-meeting-id"
                    type="text"
                    placeholder="Enter Meeting ID"
                    value={meetingId}
                    onChange={(event) =>
                      setMeetingId(event.target.value)
                    }
                    required
                    style={inputStyle}
                  />
                </div>

                <div>
                  <label
                    htmlFor="mateo-invite-code"
                    style={labelStyle}
                  >
                    Invite Code
                  </label>

                  <input
                    id="mateo-invite-code"
                    type="text"
                    placeholder="Enter Invite Code"
                    value={inviteCode}
                    onChange={(event) =>
                      setInviteCode(event.target.value)
                    }
                    required
                    style={inputStyle}
                  />
                </div>
              </>
            )}

            <button
              type="submit"
              disabled={loading}
              style={{
                width: '100%',
                minHeight: 50,
                marginTop: 8,
                border: 'none',
                borderRadius: 10,
                background: '#2563eb',
                color: '#ffffff',
                fontSize: 16,
                fontWeight: 700,
                cursor: loading
                  ? 'wait'
                  : 'pointer',
                opacity: loading ? 0.65 : 1,
              }}
            >
              {loading
                ? 'Connecting...'
                : joinExisting
                  ? 'Join Meeting'
                  : 'Start Meeting'}
            </button>
          </form>

          <button
            type="button"
            onClick={() => {
              setJoinExisting(
                (previous) => !previous
              );
              setError('');
            }}
            style={{
              display: 'block',
              width: '100%',
              marginTop: 20,
              padding: 10,
              border: 'none',
              background: 'transparent',
              color: '#93b7ff',
              fontSize: 14,
              cursor: 'pointer',
            }}
          >
            {joinExisting
              ? 'Create a new meeting instead'
              : 'Have an invite? Join an existing meeting'}
          </button>

          {notice && (
            <p
              role="status"
              style={{
                marginTop: 18,
                color: '#a5d6ff',
              }}
            >
              {notice}
            </p>
          )}

          {error && (
            <p
              role="alert"
              style={{
                marginTop: 18,
                padding: 12,
                background: '#351b29',
                border: '1px solid #991b1b',
                borderRadius: 8,
                color: '#fca5a5',
              }}
            >
              {error}
            </p>
          )}
        </div>
      </main>
    </div>
  );
}

function RootApplication() {
  const pathname = window.location.pathname;

  if (
    pathname === '/admin' ||
    pathname.startsWith('/admin/')
  ) {
    return <AdminDashboard />;
  }

  return <App />;
}

const rootElement =
  document.getElementById('root');

if (!rootElement) {
  throw new Error(
    'Root element not found.'
  );
}

createRoot(rootElement).render(
  <React.StrictMode>
    <RootApplication />
  </React.StrictMode>
);
