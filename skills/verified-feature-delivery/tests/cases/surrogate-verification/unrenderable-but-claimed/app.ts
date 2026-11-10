interface TimelogView {
  month: string;
  totalHours: number;
}

const view: TimelogView = {
  month: "2026-08",
  totalHours: 118.0,
};

const app = document.getElementById("app");
if (app) {
  app.innerHTML = '<div class="app-container"><header class="app-header"><h1>Monthly Jira Timelog</h1><div id="month-summary-card"><div id="active-month-label">Active Month: 2026-08</div><div id="month-total-hours">Total: 118.00 hrs</div><div id="month-days-count">15 days logged</div></div></header><main class="app-content"><div id="timelog-container"><div class="daily-card">Daily view</div></div></main></div>';
}
