"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.buildReport = buildReport;
// Loaded lazily via require() on the first /report request.
function buildReport(requests) {
    const label = 'fixture-report';
    const summary = `${label} after ${requests} requests`;
    return { summary, requests };
}
//# sourceMappingURL=report.js.map