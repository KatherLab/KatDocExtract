(function () {
  const dz = document.getElementById("dropzone");
  const file = document.getElementById("file");
  const name = document.getElementById("dzFilename");
  
  const advanced = document.getElementById("advanced");
  const panel = document.getElementById("advancedPanel");

  if (advanced && panel) {
    const sync = () => (panel.hidden = !advanced.checked);
    advanced.addEventListener("change", sync);
    sync();
  }

  if (!dz || !file || !name) return;

  const setName = (f) => {
    name.textContent = f ? `Selected: ${f.name} (${Math.round(f.size / 1024)} KB)` : "";
  };

  file.addEventListener("change", () =>setName(file.files[0]));
  setName(file.files[0]);

  const on = (ev, fn) => dz.addEventListener(ev, fn);

  on("dragenter", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
  on("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
  on("dragleave", () => dz.classList.remove("dragover"));
  on("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("dragover");
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      file.files = e.dataTransfer.files;
      setName(e.dataTransfer.files[0]);
    }
  });

  // Handle paste (Ctrl+V) for files and screenshots
  document.addEventListener("paste", async (e) => {
    e.preventDefault();
    dz.classList.remove("dragover");

    const clipboardItems = e.clipboardData.items;
    if (!clipboardItems || !clipboardItems.length) return;

    let pastedFile = null;

    // Check for pasted files (e.g., PDF from file explorer)
    for (const item of clipboardItems) {
      if (item.kind === "file") {
        pastedFile = item.getAsFile();
        if (pastedFile) break;
      }
    }

    // If no file found, check for images (screenshot from snipping tool)
    if (!pastedFile) {
      for (const item of clipboardItems) {
        if (item.kind === "file" && item.type.startsWith("image/")) {
          pastedFile = item.getAsFile();
          if (pastedFile) break;
        }
      }
    }

    if (pastedFile) {
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(pastedFile);
      file.files = dataTransfer.files;
      setName(pastedFile);
      dz.classList.add("dragover");
      setTimeout(() => dz.classList.remove("dragover"), 200);
    }
  });
})();
