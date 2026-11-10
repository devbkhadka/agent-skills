// Loaded lazily via require() on the first /report request.
export function buildReport(requests: number): { summary: string; requests: number } {
  const label = 'fixture-report';
  const summary = `${label} after ${requests} requests`;
  return { summary, requests };
}
