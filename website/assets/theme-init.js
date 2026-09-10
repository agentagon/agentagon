(() => {
  let theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  try {
    const saved = localStorage.getItem("agentagon-theme");
    if (saved === "light" || saved === "dark") theme = saved;
  } catch { /* Use the system preference when storage is unavailable. */ }
  document.documentElement.dataset.theme = theme;
})();
