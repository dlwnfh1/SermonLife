(function () {
  function setupDailyInlineToggles() {
    const groups = document.querySelectorAll(".inline-group .inline-related");

    groups.forEach((group) => {
      const dayField = group.querySelector('input[name$="-day_number"]');
      const heading = group.querySelector("h3");
      if (!dayField || !heading) {
        return;
      }

      group.classList.add("daily-collapsible");

      let body = group.querySelector(".inline-body");
      if (!body) {
        body = document.createElement("div");
        body.className = "inline-body";

        const children = Array.from(group.children).filter((child) => child !== heading);
        children.forEach((child) => body.appendChild(child));
        group.appendChild(body);
      }

      const dayValue = dayField.value || "?";
      const titleField = group.querySelector('input[name$="-title"]');
      const titleValue = titleField && titleField.value ? ` | ${titleField.value}` : "";
      heading.textContent = `Day ${dayValue}${titleValue}`;

      if (!group.dataset.toggleBound) {
        group.dataset.toggleBound = "1";
        group.classList.add("is-collapsed");
        heading.addEventListener("click", function () {
          group.classList.toggle("is-collapsed");
        });
      }
    });
  }

  document.addEventListener("DOMContentLoaded", setupDailyInlineToggles);
})();
