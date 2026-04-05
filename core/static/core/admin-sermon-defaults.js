(function () {
  function deriveTitleFromPath(value) {
    if (!value) {
      return "";
    }
    var normalized = value.replace(/\\/g, "/");
    var fileName = normalized.split("/").pop() || "";
    return fileName.replace(/\.[^.]+$/, "").trim();
  }

  function setupSermonDefaults() {
    var titleField = document.getElementById("id_title");
    var sourceMediaSelect = document.getElementById("id_source_media_asset");
    var audioField = document.getElementById("id_audio_file");

    if (!titleField) {
      return;
    }

    function syncTitleFromPath(pathValue) {
      var derived = deriveTitleFromPath(pathValue);
      if (!derived) {
        return;
      }

      if (!titleField.value || titleField.dataset.autoDerived === "1") {
        titleField.value = derived;
        titleField.dataset.autoDerived = "1";
      }
    }

    titleField.addEventListener("input", function () {
      titleField.dataset.autoDerived = "0";
    });

    if (sourceMediaSelect) {
      sourceMediaSelect.addEventListener("change", function () {
        var selected = sourceMediaSelect.options[sourceMediaSelect.selectedIndex];
        if (!selected || !selected.value) {
          return;
        }
        syncTitleFromPath(selected.textContent || "");
      });

      var current = sourceMediaSelect.options[sourceMediaSelect.selectedIndex];
      if (current && current.value) {
        syncTitleFromPath(current.textContent || "");
      }
    }

    if (audioField) {
      audioField.addEventListener("change", function () {
        var value = audioField.value || "";
        syncTitleFromPath(value);
      });
    }
  }

  document.addEventListener("DOMContentLoaded", setupSermonDefaults);
})();
