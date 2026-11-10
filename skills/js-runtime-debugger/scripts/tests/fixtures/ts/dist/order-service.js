"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.Order = void 0;
exports.placeOrder = placeOrder;
class Order {
    constructor(id) {
        this.id = id;
        this.lines = [];
        this.status = 'draft';
    }
    addLine(line) {
        if (this.status !== 'draft') {
            throw new Error(`Order ${this.id} is ${this.status}`);
        }
        // TypeScript preserves this comment in the output.
        this.lines.push(Object.assign({}, line));
    }
    total() {
        return this.lines.reduce((sum, line) => sum + line.quantity * line.unitPrice, 0);
    }
    place() {
        if (this.lines.length === 0)
            throw new Error('empty order');
        this.status = 'placed';
    }
}
exports.Order = Order;
async function placeOrder(order, notify) {
    order.place();
    const amount = order.total();
    await notify(order.id);
    return amount;
}
//# sourceMappingURL=order-service.js.map