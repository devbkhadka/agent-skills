export interface OrderLine {
  sku: string;
  quantity: number;
  unitPrice: number;
}

export type OrderStatus = 'draft' | 'placed' | 'cancelled';

export class Order {
  private readonly lines: OrderLine[] = [];
  private status: OrderStatus = 'draft';

  constructor(public readonly id: string) {}

  addLine(line: OrderLine): void {
    if (this.status !== 'draft') {
      throw new Error(`Order ${this.id} is ${this.status}`);
    }
    // TypeScript preserves this comment in the output.

    this.lines.push({ ...line });
  }

  total(): number {
    return this.lines.reduce(
      (sum, line) => sum + line.quantity * line.unitPrice,
      0,
    );
  }

  place(): void {
    if (this.lines.length === 0) throw new Error('empty order');
    this.status = 'placed';
  }
}

export async function placeOrder(order: Order, notify: (id: string) => Promise<void>): Promise<number> {
  order.place();
  const amount = order.total();
  await notify(order.id);
  return amount;
}
