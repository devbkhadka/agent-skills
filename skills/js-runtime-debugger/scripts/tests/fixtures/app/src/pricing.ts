export interface Quote {
  sku: string;
  qty: number;
  unitPrice: number;
  discount: number;
  total: number;
}

const PRICES: Record<string, number> = { 'sku-1': 25, 'sku-2': 40 };

export function priceOrder(sku: string, qty: number): Quote {
  const unitPrice = PRICES[sku] ?? 0;
  const subtotal = unitPrice * qty;
  const discount = qty >= 10 ? subtotal * 0.1 : 0;
  const total = subtotal - discount;
  return { sku, qty, unitPrice, discount, total };
}

export function failHard(reason: string): never {
  throw new Error(`boom: ${reason}`);
}
