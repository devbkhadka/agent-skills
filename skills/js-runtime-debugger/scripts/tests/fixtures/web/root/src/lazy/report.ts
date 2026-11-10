export function buildReport(label: string): string {
  const stamp: string = `report:${label}`;
  const length: number = stamp.length;
  return `${stamp} (${length})`;
}
