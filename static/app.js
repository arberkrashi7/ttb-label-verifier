// TTB Label Verifier — frontend script (Step 2).
// Handles the image picker (click + drag-and-drop) and form submission.
// The actual verification call is added in a later step.

(function () {
  "use strict";

  const form = document.getElementById("verify-form");
  const dropZone = document.getElementById("drop-zone");
  const dropPrompt = document.getElementById("drop-prompt");
  const input = document.getElementById("image-input");
  const preview = document.getElementById("preview");
  const fileRow = document.getElementById("file-row");
  const fileName = document.getElementById("file-name");
  const removeBtn = document.getElementById("remove-image");
  const verifyBtn = document.getElementById("verify-btn");
  const status = document.getElementById("status");
  const results = document.getElementById("results");
  const overall = document.getElementById("overall");
  const resultRows = document.getElementById("result-rows");
  const timing = document.getElementById("timing");

  // Maps a status string to a short CSS-class suffix for color coding.
  function statusClass(statusText) {
    if (statusText === "PASS") return "pass";
    if (statusText === "FAIL") return "fail";
    if (statusText === "NEEDS REVIEW") return "review";
    return "unknown"; // CANNOT VERIFY
  }

  let selectedFile = null;

  function setStatus(message, kind) {
    status.textContent = message;
    status.className = "status" + (kind ? " " + kind : "");
    // The button is at the bottom of a long form — bring an error into view.
    if (kind === "error") {
      status.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  // ---- Image selection ----

  function showImage(file) {
    selectedFile = file;
    const url = URL.createObjectURL(file);
    preview.src = url;
    preview.hidden = false;
    dropPrompt.hidden = true;
    fileName.textContent = file.name;
    fileRow.hidden = false;
    setStatus("", "");
  }

  function clearImage() {
    selectedFile = null;
    input.value = "";
    if (preview.src) {
      URL.revokeObjectURL(preview.src);
    }
    preview.removeAttribute("src");
    preview.hidden = true;
    dropPrompt.hidden = false;
    fileRow.hidden = true;
  }

  const MAX_BYTES = 15 * 1024 * 1024; // keep in sync with the server limit

  function handleFiles(files) {
    if (!files || files.length === 0) {
      return;
    }
    const file = files[0];
    if (!file.type.startsWith("image/")) {
      setStatus("That file is not an image. Please choose a JPG or PNG photo.", "error");
      return;
    }
    if (file.size > MAX_BYTES) {
      setStatus("That image is too large. Please use one under 15 MB.", "error");
      return;
    }
    showImage(file);
  }

  input.addEventListener("change", function () {
    handleFiles(input.files);
  });

  removeBtn.addEventListener("click", function (event) {
    // Stop the click from bubbling to the drop zone (which opens the picker).
    event.preventDefault();
    event.stopPropagation();
    clearImage();
  });

  // ---- Drag and drop ----

  ["dragenter", "dragover"].forEach(function (name) {
    dropZone.addEventListener(name, function (event) {
      event.preventDefault();
      dropZone.classList.add("dragover");
    });
  });

  ["dragleave", "dragend", "drop"].forEach(function (name) {
    dropZone.addEventListener(name, function (event) {
      event.preventDefault();
      dropZone.classList.remove("dragover");
    });
  });

  dropZone.addEventListener("drop", function (event) {
    if (event.dataTransfer && event.dataTransfer.files) {
      handleFiles(event.dataTransfer.files);
    }
  });

  // ---- Render the verification result as a scannable checklist ----

  // A status icon: check (PASS), X (FAIL), flag (NEEDS REVIEW), ? (CANNOT VERIFY).
  function iconFor(statusText) {
    if (statusText === "PASS") return "✓"; // ✓
    if (statusText === "FAIL") return "✕"; // ✕
    if (statusText === "NEEDS REVIEW") return "⚑"; // ⚑
    return "?"; // CANNOT VERIFY
  }

  function truncate(text, max) {
    if (text.length <= max) return text;
    return text.slice(0, max - 1).trimEnd() + "…";
  }

  function renderResults(payload) {
    const verification = payload.verification;

    // Overall banner: big icon + status word.
    overall.className = "overall " + statusClass(verification.overall);
    overall.textContent = "";
    const oIcon = document.createElement("span");
    oIcon.className = "overall-icon";
    oIcon.textContent = iconFor(verification.overall);
    oIcon.setAttribute("aria-hidden", "true");
    const oText = document.createElement("span");
    oText.textContent = verification.overall;
    overall.appendChild(oIcon);
    overall.appendChild(oText);

    // One checklist row per field, built fresh each time.
    resultRows.textContent = "";
    verification.fields.forEach(function (field) {
      const cls = statusClass(field.status);

      const row = document.createElement("div");
      row.className = "check-row " + cls;

      const icon = document.createElement("div");
      icon.className = "check-icon";
      icon.textContent = iconFor(field.status);
      icon.setAttribute("aria-hidden", "true");
      row.appendChild(icon);

      const body = document.createElement("div");
      body.className = "check-body";

      const top = document.createElement("div");
      top.className = "check-top";
      const name = document.createElement("span");
      name.className = "check-field";
      name.textContent = field.label;
      const pill = document.createElement("span");
      pill.className = "check-pill " + cls;
      pill.textContent = field.status + " · " + field.confidence;
      top.appendChild(name);
      top.appendChild(pill);
      body.appendChild(top);

      const reason = document.createElement("p");
      reason.className = "check-reason";
      reason.textContent = field.reason;
      body.appendChild(reason);

      const detail = document.createElement("p");
      detail.className = "check-detail";
      const onLabel = (field.label_value || "").trim() || "(not found)";
      const onForm = (field.application_value || "").trim() || "(blank)";
      detail.textContent =
        "Label: " + truncate(onLabel, 70) + "   •   Form: " + truncate(onForm, 70);
      body.appendChild(detail);

      row.appendChild(body);
      resultRows.appendChild(row);
    });

    const seconds = (payload.elapsed_ms / 1000).toFixed(1);
    timing.textContent = "Checked in " + seconds + " seconds.";
    results.hidden = false;
  }

  // ---- Form submission: send image + values, show verdicts ----

  form.addEventListener("submit", async function (event) {
    event.preventDefault();

    if (!selectedFile) {
      setStatus("Please add a label image first.", "error");
      return;
    }

    results.hidden = true;
    setStatus("Checking the label...", "");
    verifyBtn.disabled = true;

    try {
      const data = new FormData(form); // includes all the text fields
      data.append("image", selectedFile);

      const response = await fetch("/api/verify", {
        method: "POST",
        body: data,
      });

      // Parse defensively — a server error might not return JSON.
      let payload = null;
      try {
        payload = await response.json();
      } catch (parseErr) {
        payload = null;
      }

      if (!response.ok) {
        const message =
          (payload && payload.error) ||
          "Something went wrong. Please try again.";
        setStatus(message, "error");
        return;
      }

      if (!payload || !payload.verification || !payload.verification.fields) {
        setStatus("We couldn't read the result. Please try again.", "error");
        return;
      }

      setStatus("", "");
      renderResults(payload);
    } catch (err) {
      setStatus("Could not reach the server. Please check it is running and try again.", "error");
    } finally {
      verifyBtn.disabled = false;
    }
  });
})();
