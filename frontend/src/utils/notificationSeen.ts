const PREFIX = "saathi:notifications:lastSeen:";

export function getLastSeenAt(token: string): number {
  const raw = localStorage.getItem(PREFIX + token);
  return raw ? Number(raw) : 0;
}

export function markNotificationsSeen(token: string, atMs: number = Date.now()): void {
  localStorage.setItem(PREFIX + token, String(atMs));
}
