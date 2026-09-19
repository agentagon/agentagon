"use strict";

// Match the landing page: light by default, with a saved explicit choice.
// Run before the stylesheet to avoid a flash of the wrong theme.
try {
  if (localStorage.getItem("agentagon-theme") === "dark") {
    document.documentElement.dataset.theme = "dark";
  }
} catch { /* The dashboard also works when browser storage is unavailable. */ }
