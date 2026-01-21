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

  const setName = () => {
    const f = file.files && file.files[0];
    name.textContent = f ? `Selected: ${f.name} (${Math.round(f.size / 1024)} KB)` : "";
  };

  file.addEventListener("change", setName);
  setName();

  const on = (ev, fn) => dz.addEventListener(ev, fn);

  on("dragenter", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
  on("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
  on("dragleave", () => dz.classList.remove("dragover"));
  on("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("dragover");
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      file.files = e.dataTransfer.files;
      setName();
    }
  });
})();
