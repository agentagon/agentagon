(() => {

      const themeToggle = document.querySelector(".theme-toggle");
      const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
      let hasThemeChoice = false;
      try {
        hasThemeChoice = ["light", "dark"].includes(localStorage.getItem("agentagon-theme"));
      } catch { /* A manual choice still works for this visit. */ }
      function applyTheme(theme) {
        document.documentElement.dataset.theme = theme;
        const dark = theme === "dark";
        themeToggle.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
        themeToggle.title = themeToggle.getAttribute("aria-label");
        document.querySelector('meta[name="theme-color"]').content = dark ? "#081521" : "#f5f8fb";
      }
      applyTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
      themeToggle.hidden = false;
      themeToggle.addEventListener("click", () => {
        hasThemeChoice = true;
        const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
        applyTheme(theme);
        try { localStorage.setItem("agentagon-theme", theme); } catch { /* Choice still works for this visit. */ }
      });
      systemTheme.addEventListener("change", (event) => {
        if (!hasThemeChoice) applyTheme(event.matches ? "dark" : "light");
      });

      // Rotate each group; assistive technology gets the complete static line.
      const benefitLine = document.querySelector(".benefit-line");
      if (!benefitLine) return;
      const benefitGroups = Array.from(document.querySelectorAll(".benefit-words"), (group) => ({
        words: group.querySelectorAll(".benefit-word"),
        index: 0,
      }));
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
      let benefitsVisible = true;
      let benefitTimer;

      function scheduleBenefit() {
        window.clearTimeout(benefitTimer);
        const enabled = !reducedMotion.matches;
        benefitLine.classList.toggle("is-rotating", enabled);
        if (!enabled) {
          benefitGroups.forEach((group) => {
            group.index = 0;
            group.words.forEach((word, index) => word.classList.toggle("is-active", index === 0));
          });
        }
        if (!benefitsVisible || document.hidden || !enabled) return;
        benefitTimer = window.setTimeout(() => {
          benefitGroups.forEach((group) => {
            group.words[group.index].classList.remove("is-active");
            group.index = (group.index + 1) % group.words.length;
            group.words[group.index].classList.add("is-active");
          });
          scheduleBenefit();
        }, 1500);
      }

      document.addEventListener("visibilitychange", scheduleBenefit);
      reducedMotion.addEventListener("change", scheduleBenefit);
      if ("IntersectionObserver" in window) {
        new IntersectionObserver(([entry]) => {
          benefitsVisible = entry.isIntersecting;
          scheduleBenefit();
        }).observe(benefitLine);
      }
      scheduleBenefit();

})();
