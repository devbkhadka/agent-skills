export function computeTotal(items: number[], factor: number): number {
  const subtotal: number = items.reduce((sum, n) => sum + n, 0);
  const total: number = subtotal * factor;
  return total;
}
