"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.priceOrder = priceOrder;
exports.failHard = failHard;
const PRICES = { 'sku-1': 25, 'sku-2': 40 };
function priceOrder(sku, qty) {
    const unitPrice = PRICES[sku] ?? 0;
    const subtotal = unitPrice * qty;
    const discount = qty >= 10 ? subtotal * 0.1 : 0;
    const total = subtotal - discount;
    return { sku, qty, unitPrice, discount, total };
}
function failHard(reason) {
    throw new Error(`boom: ${reason}`);
}
//# sourceMappingURL=pricing.js.map