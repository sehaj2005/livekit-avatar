import React, {
  useCallback,
  useEffect,
  useState,
} from 'react';

import './admin.css';
import './admin-users.css';


const API =
  import.meta.env.VITE_API_URL ??
  'http://127.0.0.1:8000';

const TOKEN_KEY = 'mateo_admin_token';
const REFRESH_INTERVAL = 10000;


type AdminAccount = {
  account_id: string;
  email: string;
  active: boolean;
  is_owner: boolean;
  created_at: number;
  last_login_at: number | null;
};

type LoginResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
  account: AdminAccount;
};

type CostSummary = {
  llm: number;
  stt: number;
  tts: number;
  total_estimated_usd: number;
  rates_configured: boolean;
};

type LiveParticipant = {
  id: string;
  name: string;
};

type Meeting = {
  meeting_id: string;
  room_name: string;
  created_at: number;
  state: string;
  live_count: number | null;
  live_participants: LiveParticipant[] | null;
};

type Overview = {
  meeting_count: number;
  total_joins: number;
  query_count: number;
  live_humans: number;
  live_count_complete: boolean;
  estimated_cost: CostSummary;
  meetings: Meeting[];
};

type Participant = {
  participant_id: string;
  display_name: string;
  joined_at: number;
  queries: number;
  online: boolean | null;
};

type ModelUsage = {
  session_id: string;
  provider: string;
  model: string;
  kind: string;
  input_tokens: number;
  output_tokens: number;
  stt_seconds: number;
  tts_characters: number;
};

type MeetingDetail = {
  meeting: {
    meeting_id: string;
    room_name: string;
    created_at: number;
    state: string;
  };
  participants: Participant[];
  live_count: number | null;
  model_usage: ModelUsage[];
  estimated_cost: CostSummary;
};

type AdminInvitation = {
  invitation_id: string;
  email: string;
  created_at: number;
  expires_at: number;
  consumed_at: number | null;
  revoked_at: number | null;
};

type InvitationResponse = {
  invitation_id: string;
  email: string;
  invite_token: string;
  expires_at: number;
};

type InvitationValidation = {
  email: string;
  expires_at: number;
};

type DashboardTab = 'overview' | 'accounts';


function formatMoney(value: number): string {
  return `$${value.toFixed(4)}`;
}


function formatNumber(value: number): string {
  return value.toLocaleString();
}


function formatDate(
  timestamp: number | null
): string {
  if (timestamp === null) {
    return 'Never';
  }

  return new Date(
    timestamp * 1000
  ).toLocaleString();
}


function formatLiveCount(
  count: number | null
): string {
  return count === null
    ? 'Unavailable'
    : String(count);
}


function getErrorMessage(
  error: unknown
): string {
  return error instanceof Error
    ? error.message
    : 'An unexpected error occurred.';
}


async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {},
  token?: string
): Promise<T> {
  const headers = new Headers(options.headers);

  if (options.body !== undefined) {
    headers.set(
      'Content-Type',
      'application/json'
    );
  }

  if (token) {
    headers.set(
      'Authorization',
      `Bearer ${token}`
    );
  }

  const response = await fetch(
    `${API}/api/v1${endpoint}`,
    {
      ...options,
      headers,
    }
  );

  if (!response.ok) {
    let detail = '';

    try {
      const body = await response.json();

      detail =
        typeof body.detail === 'string'
          ? body.detail
          : '';
    } catch {
      detail = '';
    }

    if (response.status === 401 && token) {
      throw new Error(
        'Your admin session has expired. Please log in again.'
      );
    }

    throw new Error(
      detail ||
      `Request failed (${response.status})`
    );
  }

  return response.json() as Promise<T>;
}


function StatCard({
  title,
  value,
  subtitle,
  icon,
}: {
  title: string;
  value: string;
  subtitle: string;
  icon: string;
}) {
  return (
    <div className="admin-stat-card">
      <div className="admin-stat-top">
        <span>{title}</span>

        <span className="admin-stat-icon">
          {icon}
        </span>
      </div>

      <strong className="admin-stat-value">
        {value}
      </strong>

      <p>{subtitle}</p>
    </div>
  );
}


function CostCard({
  title,
  amount,
  configured,
  description,
}: {
  title: string;
  amount: number;
  configured: boolean;
  description: string;
}) {
  return (
    <div className="admin-cost-card">
      <h3>{title}</h3>

      <strong>
        {configured
          ? formatMoney(amount)
          : 'Not configured'}
      </strong>

      <p>{description}</p>
    </div>
  );
}


function InvitationAcceptance({
  token,
}: {
  token: string;
}) {
  const [invitation, setInvitation] =
    useState<InvitationValidation | null>(null);

  const [password, setPassword] =
    useState('');

  const [confirmPassword, setConfirmPassword] =
    useState('');

  const [loading, setLoading] =
    useState(true);

  const [submitting, setSubmitting] =
    useState(false);

  const [error, setError] =
    useState('');

  const [success, setSuccess] =
    useState(false);


  useEffect(() => {
    let cancelled = false;

    async function validate() {
      try {
        const result =
          await apiRequest<InvitationValidation>(
            `/auth/invitations/validate?token=${encodeURIComponent(token)}`
          );

        if (!cancelled) {
          setInvitation(result);
        }
      } catch (err) {
        if (!cancelled) {
          setError(getErrorMessage(err));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void validate();

    return () => {
      cancelled = true;
    };
  }, [token]);


  async function acceptInvitation(
    event: React.FormEvent<HTMLFormElement>
  ) {
    event.preventDefault();
    setError('');

    if (password.length < 12) {
      setError(
        'Password must contain at least 12 characters.'
      );
      return;
    }

    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }

    setSubmitting(true);

    try {
      await apiRequest<{ ok: boolean }>(
        '/auth/invitations/accept',
        {
          method: 'POST',
          body: JSON.stringify({
            token,
            password,
          }),
        }
      );

      setSuccess(true);
      setPassword('');
      setConfirmPassword('');
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }


  return (
    <main className="admin-page">
      <section className="admin-login">
        <div className="admin-login-icon">
          🛡️
        </div>

        <h1>Mateo Admin Invitation</h1>

        {loading ? (
          <p>Validating invitation...</p>
        ) : success ? (
          <>
            <p>
              Your Super Admin account was
              created successfully.
            </p>

            <a href="/admin">
              Continue to Login
            </a>
          </>
        ) : invitation ? (
          <>
            <p>
              You've been invited as a Super Admin.
            </p>

            <p>
              <strong>
                {invitation.email}
              </strong>
            </p>

            <form onSubmit={acceptInvitation}>
              <label htmlFor="new-password">
                Create Password
              </label>

              <input
                id="new-password"
                type="password"
                value={password}
                onChange={(event) =>
                  setPassword(event.target.value)
                }
                minLength={12}
                maxLength={256}
                autoComplete="new-password"
                required
              />

              <label htmlFor="confirm-password">
                Confirm Password
              </label>

              <input
                id="confirm-password"
                type="password"
                value={confirmPassword}
                onChange={(event) =>
                  setConfirmPassword(event.target.value)
                }
                autoComplete="new-password"
                required
              />

              <button
                type="submit"
                disabled={submitting}
              >
                {submitting
                  ? 'Creating Account...'
                  : 'Activate Super Admin'}
              </button>
            </form>
          </>
        ) : (
          <p>
            This invitation is invalid,
            expired, or already used.
          </p>
        )}

        {error && (
          <p className="admin-error" role="alert">
            {error}
          </p>
        )}

        <a href="/admin">
          Back to Admin Login
        </a>
      </section>
    </main>
  );
}


export default function AdminDashboard() {
  const params = new URLSearchParams(
    window.location.search
  );

  const invitationToken =
    params.get('invite') || '';

  if (invitationToken) {
    return (
      <InvitationAcceptance
        token={invitationToken}
      />
    );
  }

  return <AdminApplication />;
}


function AdminApplication() {
  const [token, setToken] = useState(
    () => sessionStorage.getItem(TOKEN_KEY) || ''
  );

  const [email, setEmail] =
    useState('');

  const [password, setPassword] =
    useState('');

  const [account, setAccount] =
    useState<AdminAccount | null>(null);

  const [loggingIn, setLoggingIn] =
    useState(false);

  const [loginError, setLoginError] =
    useState('');

  const [tab, setTab] =
    useState<DashboardTab>('overview');

  const [overview, setOverview] =
    useState<Overview | null>(null);

  const [selectedMeeting, setSelectedMeeting] =
    useState('');

  const [meetingDetail, setMeetingDetail] =
    useState<MeetingDetail | null>(null);

  const [accounts, setAccounts] =
    useState<AdminAccount[]>([]);

  const [invitations, setInvitations] =
    useState<AdminInvitation[]>([]);

  const [inviteEmail, setInviteEmail] =
    useState('');

  const [generatedLink, setGeneratedLink] =
    useState('');

  const [loading, setLoading] =
    useState(false);

  const [resetting, setResetting] =
    useState(false);

  const [actionLoading, setActionLoading] =
    useState(false);

  const [error, setError] =
    useState('');

  const [notice, setNotice] =
    useState('');

  const [lastUpdated, setLastUpdated] =
    useState<Date | null>(null);


  const logout = useCallback(() => {
    sessionStorage.removeItem(TOKEN_KEY);

    setToken('');
    setAccount(null);
    setOverview(null);
    setAccounts([]);
    setInvitations([]);
    setMeetingDetail(null);
    setSelectedMeeting('');
    setError('');
    setNotice('');
    setLastUpdated(null);
  }, []);


  async function handleLogin(
    event: React.FormEvent<HTMLFormElement>
  ) {
    event.preventDefault();

    setLoggingIn(true);
    setLoginError('');

    try {
      const result =
        await apiRequest<LoginResponse>(
          '/auth/login',
          {
            method: 'POST',
            body: JSON.stringify({
              email: email.trim(),
              password,
            }),
          }
        );

      sessionStorage.setItem(
        TOKEN_KEY,
        result.access_token
      );

      setAccount(result.account);
      setToken(result.access_token);
      setPassword('');
    } catch (err) {
      setLoginError(getErrorMessage(err));
    } finally {
      setLoggingIn(false);
    }
  }


  const loadDashboard = useCallback(
    async () => {
      if (!token) {
        return;
      }

      const [current, summary] =
        await Promise.all([
          apiRequest<AdminAccount>(
            '/admin/me',
            {},
            token
          ),
          apiRequest<Overview>(
            '/admin/overview',
            {},
            token
          ),
        ]);

      setAccount(current);
      setOverview(summary);
      setLastUpdated(new Date());
    },
    [token]
  );


  const loadAccounts = useCallback(
    async () => {
      if (!token) {
        return;
      }

      const [accountResult, inviteResult] =
        await Promise.all([
          apiRequest<{
            accounts: AdminAccount[];
          }>(
            '/admin/accounts',
            {},
            token
          ),
          apiRequest<{
            invitations: AdminInvitation[];
          }>(
            '/admin/invitations',
            {},
            token
          ),
        ]);

      setAccounts(accountResult.accounts);
      setInvitations(inviteResult.invitations);
    },
    [token]
  );


  const loadMeetingDetail = useCallback(
    async (meetingId: string) => {
      if (!token || !meetingId) {
        return;
      }

      const result =
        await apiRequest<MeetingDetail>(
          `/admin/meetings/${encodeURIComponent(meetingId)}`,
          {},
          token
        );

      setMeetingDetail(result);
    },
    [token]
  );


  useEffect(() => {
    if (!token) {
      return;
    }

    let cancelled = false;
    let busy = false;

    async function refresh() {
      if (busy || cancelled) {
        return;
      }

      busy = true;

      try {
        await loadDashboard();

        if (!cancelled) {
          setError('');
        }
      } catch (err) {
        if (!cancelled) {
          const message = getErrorMessage(err);

          setError(message);

          if (
            message.toLowerCase().includes(
              'session has expired'
            )
          ) {
            logout();
            setLoginError(message);
          }
        }
      } finally {
        busy = false;
      }
    }

    void refresh();

    const timer = window.setInterval(
      () => void refresh(),
      REFRESH_INTERVAL
    );

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [token, loadDashboard, logout]);


  useEffect(() => {
    if (!token || tab !== 'accounts') {
      return;
    }

    void loadAccounts().catch(
      (err) => setError(getErrorMessage(err))
    );
  }, [token, tab, loadAccounts]);


  useEffect(() => {
    if (!token || !selectedMeeting) {
      setMeetingDetail(null);
      return;
    }

    let cancelled = false;

    async function refreshDetail() {
      try {
        await loadMeetingDetail(selectedMeeting);
      } catch (err) {
        if (!cancelled) {
          setError(getErrorMessage(err));
        }
      }
    }

    void refreshDetail();

    const timer = window.setInterval(
      () => void refreshDetail(),
      REFRESH_INTERVAL
    );

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    token,
    selectedMeeting,
    loadMeetingDetail,
  ]);


  async function refreshNow() {
    setLoading(true);
    setError('');

    try {
      await loadDashboard();

      if (tab === 'accounts') {
        await loadAccounts();
      }

      if (selectedMeeting) {
        await loadMeetingDetail(selectedMeeting);
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }


  async function resetAllStatistics() {
    if (!account?.is_owner) {
      setError(
        'Only the Primary Super Admin can reset statistics.'
      );
      return;
    }

    if (!token) {
      setError('Please log in again.');
      return;
    }

    const confirmed = window.confirm(
      'WARNING: This will permanently delete all Mateo ' +
      'meeting statistics, participant records, AI queries, ' +
      'and recorded AI usage.\n\n' +
      'Super Admin accounts will be preserved.\n\n' +
      'All active Mateo rooms must be ended before resetting.\n\n' +
      'Do you want to continue?'
    );

    if (!confirmed) {
      return;
    }

    const confirmation = window.prompt(
      'Type RESET in capital letters to confirm:'
    );

    if (confirmation !== 'RESET') {
      return;
    }

    setResetting(true);
    setError('');
    setNotice('');

    try {
      const result = await apiRequest<{
        success: boolean;
        message: string;
      }>(
        '/admin/reset-analytics',
        {
          method: 'POST',
          body: JSON.stringify({
            confirmation: 'RESET',
          }),
        },
        token
      );

      if (!result.success) {
        throw new Error(
          'The backend did not confirm the reset.'
        );
      }

      setSelectedMeeting('');
      setMeetingDetail(null);
      setOverview(null);

      await loadDashboard();

      setNotice(
        'Statistics reset successfully. All meeting analytics now start from zero.'
      );
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setResetting(false);
    }
  }


  async function createInvitation(
    event: React.FormEvent<HTMLFormElement>
  ) {
    event.preventDefault();

    setActionLoading(true);
    setError('');
    setNotice('');
    setGeneratedLink('');

    try {
      const result =
        await apiRequest<InvitationResponse>(
          '/admin/invitations',
          {
            method: 'POST',
            body: JSON.stringify({
              email: inviteEmail.trim(),
            }),
          },
          token
        );

      const link =
        `${window.location.origin}/admin?invite=` +
        encodeURIComponent(result.invite_token);

      setGeneratedLink(link);
      setInviteEmail('');

      setNotice(
        'Invitation created. Share the link securely.'
      );

      await loadAccounts();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setActionLoading(false);
    }
  }


  async function copyInvitation() {
    if (!generatedLink) {
      return;
    }

    try {
      await navigator.clipboard.writeText(
        generatedLink
      );

      setNotice('Invitation link copied.');
    } catch {
      setError(
        'Could not copy automatically. Copy the link manually.'
      );
    }
  }


  async function changeAccount(
    accountId: string,
    action: 'enable' | 'disable' | 'revoke-sessions'
  ) {
    if (!token) {
      return;
    }

    const descriptions = {
      enable: 'enable this account',
      disable: 'disable this account',
      'revoke-sessions': 'revoke all existing sessions',
    };

    if (
      !window.confirm(
        `Are you sure you want to ${descriptions[action]}?`
      )
    ) {
      return;
    }

    setActionLoading(true);
    setError('');
    setNotice('');

    try {
      await apiRequest<{ ok: boolean }>(
        `/admin/accounts/${encodeURIComponent(accountId)}/${action}`,
        {
          method: 'POST',
        },
        token
      );

      setNotice('Account updated successfully.');
      await loadAccounts();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setActionLoading(false);
    }
  }


  async function revokeInvitation(
    invitationId: string
  ) {
    if (
      !window.confirm(
        'Revoke this invitation?'
      )
    ) {
      return;
    }

    setActionLoading(true);
    setError('');

    try {
      await apiRequest<{ ok: boolean }>(
        `/admin/invitations/${encodeURIComponent(invitationId)}/revoke`,
        {
          method: 'POST',
        },
        token
      );

      setNotice('Invitation revoked.');
      await loadAccounts();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setActionLoading(false);
    }
  }


  if (!token) {
    return (
      <main className="admin-page">
        <section className="admin-login">
          <div className="admin-login-icon">
            🛡️
          </div>

          <h1>Mateo Super Admin</h1>

          <p>
            Sign in to manage administrators,
            meetings and AI analytics.
          </p>

          <form onSubmit={handleLogin}>
            <label htmlFor="admin-email">
              Email Address
            </label>

            <input
              id="admin-email"
              type="email"
              value={email}
              onChange={(event) =>
                setEmail(event.target.value)
              }
              placeholder="admin@example.com"
              autoComplete="username"
              required
            />

            <label htmlFor="admin-password">
              Password
            </label>

            <input
              id="admin-password"
              type="password"
              value={password}
              onChange={(event) =>
                setPassword(event.target.value)
              }
              placeholder="Enter your password"
              autoComplete="current-password"
              required
            />

            <button
              type="submit"
              disabled={
                loggingIn ||
                !email.trim() ||
                !password
              }
            >
              {loggingIn
                ? 'Signing In...'
                : 'Sign In'}
            </button>
          </form>

          {loginError && (
            <p className="admin-error" role="alert">
              {loginError}
            </p>
          )}

          <a href="/">
            Back to Meeting
          </a>
        </section>
      </main>
    );
  }


  const cost = overview?.estimated_cost;

  const activeMeetings =
    overview?.meetings.filter(
      (meeting) =>
        meeting.live_count !== null &&
        meeting.live_count > 0
    ).length ?? 0;

  const selectedData =
    meetingDetail?.meeting.meeting_id === selectedMeeting
      ? meetingDetail
      : null;


  return (
    <main className="admin-page">
      <header className="admin-header">
        <div>
          <h1>Mateo Super Admin</h1>

          <p>
            {account?.email ||
              'Administration Dashboard'}
          </p>
        </div>

        <div className="admin-header-actions">
          <a href="/">
            Open Meeting
          </a>

          <button
            type="button"
            onClick={() => void refreshNow()}
            disabled={loading || resetting}
          >
            {loading
              ? 'Refreshing...'
              : 'Refresh'}
          </button>

          {account?.is_owner && (
            <button
              type="button"
              className="admin-reset-button"
              onClick={() =>
                void resetAllStatistics()
              }
              disabled={resetting || loading}
            >
              {resetting
                ? 'Resetting...'
                : 'Reset Statistics'}
            </button>
          )}

          <button
            type="button"
            className="admin-logout"
            onClick={logout}
            disabled={resetting}
          >
            Logout
          </button>
        </div>
      </header>


      <div className="admin-content">
        <nav className="admin-header-actions">
          <button
            type="button"
            aria-pressed={tab === 'overview'}
            onClick={() => setTab('overview')}
          >
            📊 Analytics Overview
          </button>

          <button
            type="button"
            aria-pressed={tab === 'accounts'}
            onClick={() => setTab('accounts')}
          >
            🛡️ Super Admin Management
          </button>
        </nav>


        {error && (
          <p className="admin-error" role="alert">
            {error}
          </p>
        )}

        {notice && (
          <p className="admin-muted" role="status">
            {notice}
          </p>
        )}


        {tab === 'overview' && (
          <>
            <section className="admin-section">
              <div className="admin-section-header">
                <h2>Analytics Overview</h2>

                <span>
                  {lastUpdated
                    ? `Updated ${lastUpdated.toLocaleTimeString()}`
                    : 'Loading...'}
                </span>
              </div>

              <div className="admin-stats-grid">
                <StatCard
                  title="Live Participants"
                  value={
                    overview
                      ? overview.live_count_complete
                        ? formatNumber(overview.live_humans)
                        : 'Unavailable'
                      : '—'
                  }
                  subtitle="Currently connected humans"
                  icon="👥"
                />

                <StatCard
                  title="Total Meetings"
                  value={
                    overview
                      ? formatNumber(overview.meeting_count)
                      : '—'
                  }
                  subtitle="Meetings created"
                  icon="📹"
                />

                <StatCard
                  title="Active Meetings"
                  value={
                    overview
                      ? formatNumber(activeMeetings)
                      : '—'
                  }
                  subtitle="Meetings with live humans"
                  icon="🟢"
                />

                <StatCard
                  title="AI Queries"
                  value={
                    overview
                      ? formatNumber(overview.query_count)
                      : '—'
                  }
                  subtitle="Recorded user questions"
                  icon="💬"
                />

                <StatCard
                  title="Participant Joins"
                  value={
                    overview
                      ? formatNumber(overview.total_joins)
                      : '—'
                  }
                  subtitle="Join records, not unique users"
                  icon="🙋"
                />

                <StatCard
                  title="Estimated AI Cost"
                  value={
                    cost
                      ? cost.rates_configured
                        ? formatMoney(
                            cost.total_estimated_usd
                          )
                        : 'Not configured'
                      : '—'
                  }
                  subtitle="LLM, STT and TTS estimates"
                  icon="💰"
                />
              </div>
            </section>


            <section className="admin-section">
              <h2>AI Cost Breakdown</h2>

              <p className="admin-muted">
                Estimated costs based on the configured
                usage rates in your backend.
              </p>

              <div className="admin-cost-grid">
                <CostCard
                  title="LLM Cost"
                  amount={cost?.llm ?? 0}
                  configured={
                    cost?.rates_configured ?? false
                  }
                  description="Input and output tokens"
                />

                <CostCard
                  title="STT Cost"
                  amount={cost?.stt ?? 0}
                  configured={
                    cost?.rates_configured ?? false
                  }
                  description="Speech recognition"
                />

                <CostCard
                  title="TTS Cost"
                  amount={cost?.tts ?? 0}
                  configured={
                    cost?.rates_configured ?? false
                  }
                  description="Speech generation"
                />
              </div>

              <p className="admin-muted">
                Simli and LiveKit infrastructure
                charges are not included.
              </p>
            </section>


            <section className="admin-section">
              <div className="admin-section-header">
                <h2>Meeting Activity</h2>

                <span>
                  {overview?.meetings.length ?? 0}
                  {' '}meetings
                </span>
              </div>

              <div className="admin-table-wrapper">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Meeting ID</th>
                      <th>Created At</th>
                      <th>Live Users</th>
                      <th>Status</th>
                      <th>Action</th>
                    </tr>
                  </thead>

                  <tbody>
                    {overview?.meetings.map(
                      (meeting) => (
                        <tr key={meeting.meeting_id}>
                          <td>
                            {meeting.meeting_id}
                          </td>

                          <td>
                            {formatDate(meeting.created_at)}
                          </td>

                          <td>
                            {formatLiveCount(meeting.live_count)}
                          </td>

                          <td>
                            <span
                              className={
                                meeting.live_count !== null &&
                                meeting.live_count > 0
                                  ? 'admin-status active'
                                  : 'admin-status'
                              }
                            >
                              {meeting.live_count === null
                                ? 'Unknown'
                                : meeting.live_count > 0
                                  ? 'Active'
                                  : 'No connected humans'}
                            </span>
                          </td>

                          <td>
                            <button
                              type="button"
                              onClick={() => {
                                setSelectedMeeting(
                                  meeting.meeting_id
                                );
                                setMeetingDetail(null);
                              }}
                            >
                              View Details
                            </button>
                          </td>
                        </tr>
                      )
                    )}

                    {overview?.meetings.length === 0 && (
                      <tr>
                        <td colSpan={5}>
                          No meetings recorded yet.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>


            {selectedMeeting && (
              <section className="admin-section">
                <div className="admin-section-header">
                  <div>
                    <h2>Meeting Details</h2>

                    <p className="admin-muted">
                      {selectedMeeting}
                    </p>
                  </div>

                  <button
                    type="button"
                    onClick={() => {
                      setSelectedMeeting('');
                      setMeetingDetail(null);
                    }}
                  >
                    Close
                  </button>
                </div>

                {!selectedData ? (
                  <p className="admin-muted">
                    Loading meeting details...
                  </p>
                ) : (
                  <>
                    <div className="admin-stats-grid">
                      <StatCard
                        title="Live Participants"
                        value={formatLiveCount(
                          selectedData.live_count
                        )}
                        subtitle="Currently connected"
                        icon="👥"
                      />

                      <StatCard
                        title="Meeting Queries"
                        value={formatNumber(
                          selectedData.participants.reduce(
                            (sum, person) =>
                              sum + person.queries,
                            0
                          )
                        )}
                        subtitle="Recorded user questions"
                        icon="💬"
                      />

                      <StatCard
                        title="Estimated AI Cost"
                        value={
                          selectedData.estimated_cost
                            .rates_configured
                            ? formatMoney(
                                selectedData.estimated_cost
                                  .total_estimated_usd
                              )
                            : 'Not configured'
                        }
                        subtitle="LLM, STT and TTS"
                        icon="💵"
                      />
                    </div>


                    <h3>Queries Per Participant</h3>

                    <div className="admin-table-wrapper">
                      <table className="admin-table">
                        <thead>
                          <tr>
                            <th>Participant</th>
                            <th>Joined At</th>
                            <th>Queries</th>
                            <th>Online</th>
                          </tr>
                        </thead>

                        <tbody>
                          {selectedData.participants.map(
                            (person) => (
                              <tr key={person.participant_id}>
                                <td>{person.display_name}</td>

                                <td>
                                  {formatDate(person.joined_at)}
                                </td>

                                <td>
                                  {formatNumber(person.queries)}
                                </td>

                                <td>
                                  <span
                                    className={
                                      person.online
                                        ? 'admin-status active'
                                        : 'admin-status'
                                    }
                                  >
                                    {person.online === null
                                      ? 'Unknown'
                                      : person.online
                                        ? 'Online'
                                        : 'Offline'}
                                  </span>
                                </td>
                              </tr>
                            )
                          )}

                          {selectedData.participants.length === 0 && (
                            <tr>
                              <td colSpan={4}>
                                No participants recorded.
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>


                    <h3>AI Model Usage</h3>

                    <div className="admin-table-wrapper">
                      <table className="admin-table">
                        <thead>
                          <tr>
                            <th>Provider</th>
                            <th>Model</th>
                            <th>Type</th>
                            <th>Input Tokens</th>
                            <th>Output Tokens</th>
                            <th>STT Seconds</th>
                            <th>TTS Characters</th>
                          </tr>
                        </thead>

                        <tbody>
                          {selectedData.model_usage.map(
                            (usage, index) => (
                              <tr
                                key={
                                  `${usage.session_id}-${usage.kind}-${index}`
                                }
                              >
                                <td>{usage.provider}</td>
                                <td>{usage.model}</td>
                                <td>
                                  {usage.kind.toUpperCase()}
                                </td>

                                <td>
                                  {formatNumber(usage.input_tokens)}
                                </td>

                                <td>
                                  {formatNumber(usage.output_tokens)}
                                </td>

                                <td>
                                  {usage.stt_seconds.toFixed(2)}
                                </td>

                                <td>
                                  {formatNumber(
                                    usage.tts_characters
                                  )}
                                </td>
                              </tr>
                            )
                          )}

                          {selectedData.model_usage.length === 0 && (
                            <tr>
                              <td colSpan={7}>
                                No model usage recorded.
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
              </section>
            )}
          </>
        )}


        {tab === 'accounts' && (
          <>
            <section className="admin-section">
              <h2>Invite Super Admin</h2>

              <p className="admin-muted">
                Generate a one-time invitation
                for another administrator.
              </p>

              <form
                className="admin-user-form"
                onSubmit={createInvitation}
              >
                <label htmlFor="invite-email">
                  Administrator Email
                </label>

                <input
                  id="invite-email"
                  type="email"
                  value={inviteEmail}
                  onChange={(event) =>
                    setInviteEmail(event.target.value)
                  }
                  placeholder="colleague@example.com"
                  required
                />

                <button
                  type="submit"
                  disabled={actionLoading}
                >
                  Create Invitation
                </button>
              </form>

              {generatedLink && (
                <div className="admin-invite-result">
                  <p>
                    Invitation created successfully.
                    This link is shown only now.
                  </p>

                  <input
                    type="text"
                    value={generatedLink}
                    readOnly
                    aria-label="Invitation link"
                  />

                  <button
                    type="button"
                    onClick={() =>
                      void copyInvitation()
                    }
                  >
                    Copy Invitation Link
                  </button>
                </div>
              )}
            </section>


            <section className="admin-section">
              <div className="admin-section-header">
                <h2>Super Admin Accounts</h2>

                <span>
                  {accounts.length} accounts
                </span>
              </div>

              <div className="admin-table-wrapper">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Email</th>
                      <th>Role</th>
                      <th>Status</th>
                      <th>Created</th>
                      <th>Last Login</th>
                      <th>Actions</th>
                    </tr>
                  </thead>

                  <tbody>
                    {accounts.map((admin) => (
                      <tr key={admin.account_id}>
                        <td>{admin.email}</td>

                        <td>
                          {admin.is_owner
                            ? 'Primary Owner'
                            : 'Super Admin'}
                        </td>

                        <td>
                          <span
                            className={
                              admin.active
                                ? 'admin-status active'
                                : 'admin-status'
                            }
                          >
                            {admin.active
                              ? 'Active'
                              : 'Disabled'}
                          </span>
                        </td>

                        <td>
                          {formatDate(admin.created_at)}
                        </td>

                        <td>
                          {formatDate(admin.last_login_at)}
                        </td>

                        <td>
                          {admin.is_owner ? (
                            <span>Protected Owner</span>
                          ) : (
                            <div className="admin-account-actions">
                              <button
                                type="button"
                                disabled={actionLoading}
                                onClick={() =>
                                  void changeAccount(
                                    admin.account_id,
                                    admin.active
                                      ? 'disable'
                                      : 'enable'
                                  )
                                }
                              >
                                {admin.active
                                  ? 'Disable'
                                  : 'Enable'}
                              </button>

                              <button
                                type="button"
                                disabled={actionLoading}
                                onClick={() =>
                                  void changeAccount(
                                    admin.account_id,
                                    'revoke-sessions'
                                  )
                                }
                              >
                                Revoke Sessions
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}

                    {accounts.length === 0 && (
                      <tr>
                        <td colSpan={6}>
                          No admin accounts loaded.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>


            <section className="admin-section">
              <h2>Invitation History</h2>

              <div className="admin-table-wrapper">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Email</th>
                      <th>Created</th>
                      <th>Expires</th>
                      <th>Status</th>
                      <th>Action</th>
                    </tr>
                  </thead>

                  <tbody>
                    {invitations.map((invitation) => {
                      const pending =
                        invitation.consumed_at === null &&
                        invitation.revoked_at === null &&
                        invitation.expires_at >
                          Date.now() / 1000;

                      const status =
                        invitation.consumed_at !== null
                          ? 'Accepted'
                          : invitation.revoked_at !== null
                            ? 'Revoked'
                            : invitation.expires_at <=
                                Date.now() / 1000
                              ? 'Expired'
                              : 'Pending';

                      return (
                        <tr key={invitation.invitation_id}>
                          <td>{invitation.email}</td>

                          <td>
                            {formatDate(invitation.created_at)}
                          </td>

                          <td>
                            {formatDate(invitation.expires_at)}
                          </td>

                          <td>{status}</td>

                          <td>
                            {pending ? (
                              <button
                                type="button"
                                disabled={actionLoading}
                                onClick={() =>
                                  void revokeInvitation(
                                    invitation.invitation_id
                                  )
                                }
                              >
                                Revoke
                              </button>
                            ) : (
                              '—'
                            )}
                          </td>
                        </tr>
                      );
                    })}

                    {invitations.length === 0 && (
                      <tr>
                        <td colSpan={5}>
                          No invitations created.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </div>
    </main>
  );
}
