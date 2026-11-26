export const isGuestPage = window.location.pathname === '/join';

export const meetingApiUrl = isGuestPage
  ? ''
  : import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000';

export function getInviteLink(meetingId: string, inviteCode: string): string {
  const configuredUrl = import.meta.env.VITE_GUEST_JOIN_URL?.trim();
  const url = isGuestPage
    ? new URL('/join', window.location.origin)
    : new URL(configuredUrl || window.location.origin);

  const isLoopback = url.hostname === 'localhost' || url.hostname === '[::1]' ||
    url.hostname.startsWith('127.');
  if (url.protocol !== 'https:' || isLoopback) {
    throw new Error('An HTTPS guest join URL is required to share this meeting with another device.');
  }

  url.searchParams.set('meeting', meetingId);
  url.searchParams.set('invite', inviteCode);
  return url.toString();
}
