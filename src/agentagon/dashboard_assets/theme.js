"use strict";

// Match the landing page: light by default, with a saved explicit choice.
// Run before the stylesheet to avoid a flash of the wrong theme.
try {
  if (localStorage.getItem("agentagon-theme") === "dark") {
    document.documentElement.dataset.theme = "dark";
  }
} catch { /* The dashboard also works when browser storage is unavailable. */ }

document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.getElementById("theme-toggle");
  const updateLabel = () => {
    const dark = document.documentElement.dataset.theme === "dark";
    const label = `Switch to ${dark ? "light" : "dark"} theme`;
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
  };
  updateLabel();
  toggle.hidden = false;
  toggle.addEventListener("click", () => {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("agentagon-theme", theme); } catch { /* Optional persistence. */ }
    updateLabel();
  });
});
