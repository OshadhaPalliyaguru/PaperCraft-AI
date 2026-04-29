const dz = document.getElementById("dropzone");
const fileInput = document.getElementById("file");
const preview = document.getElementById("preview");
const generateBtn = document.getElementById("generateBtn");
const resetBtn = document.getElementById("resetBtn");
const statusText = document.getElementById("statusText");
const barFill = document.getElementById("barFill");
const downloads = document.getElementById("downloads");
const dlPdf = document.getElementById("dlPdf");
const dlGlb = document.getElementById("dlGlb");
const errorBox = document.getElementById("errorBox");

let selectedFile = null;
let pollTimer = null;

function setSelected(file) {
  selectedFile = file;
  if (!file) {
    dz.classList.remove("has-image");
    preview.removeAttribute("src");
    generateBtn.disabled = true;
    resetBtn.disabled = true;
    return;
  }
  preview.src = URL.createObjectURL(file);
  dz.classList.add("has-image");
  generateBtn.disabled = false;
  resetBtn.disabled = false;
  hideError();
  hideDownloads();
  setStatus("Ready. Click Generate to start.", 0);
}

function hideError() {
  errorBox.hidden = true;
  errorBox.textContent = "";
}

function showError(msg) {
  errorBox.hidden = false;
  errorBox.textContent = msg;
}

function setStatus(text, pct) {
  statusText.textContent = text;
  if (typeof pct === "number") {
    barFill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  }
}

function hideDownloads() {
  downloads.hidden = true;
  dlPdf.removeAttribute("href");
  dlGlb.removeAttribute("href");
}

function showDownloads(jobId, hasGlb) {
  downloads.hidden = false;
  dlPdf.href = `/api/jobs/${jobId}/template.pdf`;
  if (hasGlb) {
    dlGlb.href = `/api/jobs/${jobId}/model.glb`;
    dlGlb.style.display = "";
  } else {
    dlGlb.style.display = "none";
  }
}

dz.addEventListener("click", () => fileInput.click());
dz.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") fileInput.click();
});
fileInput.addEventListener("change", (e) => {
  const f = e.target.files?.[0];
  if (f) setSelected(f);
});
["dragover", "dragenter"].forEach((ev) =>
  dz.addEventListener(ev, (e) => {
    e.preventDefault();
    dz.classList.add("drag");
  })
);
["dragleave", "drop"].forEach((ev) =>
  dz.addEventListener(ev, (e) => {
    e.preventDefault();
    dz.classList.remove("drag");
  })
);
dz.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files?.[0];
  if (f && f.type.startsWith("image/")) setSelected(f);
});

resetBtn.addEventListener("click", () => {
  if (pollTimer) clearInterval(pollTimer);
  setSelected(null);
  setStatus("Idle", 0);
  hideDownloads();
  hideError();
});

generateBtn.addEventListener("click", async () => {
  if (!selectedFile) return;
  generateBtn.disabled = true;
  hideError();
  hideDownloads();
  setStatus("Uploading...", 5);

  const fd = new FormData();
  fd.append("image", selectedFile);

  try {
    const r = await fetch("/api/jobs", { method: "POST", body: fd });
    if (!r.ok) {
      const t = await r.text();
      throw new Error(`Upload failed: ${r.status} ${t}`);
    }
    const { job_id } = await r.json();
    pollJob(job_id);
  } catch (e) {
    showError(String(e.message || e));
    generateBtn.disabled = false;
    setStatus("Failed", 0);
  }
});

async function pollJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const r = await fetch(`/api/jobs/${jobId}`);
      if (!r.ok) return;
      const job = await r.json();
      setStatus(job.message || job.status, job.progress);
      if (job.status === "done") {
        clearInterval(pollTimer);
        showDownloads(jobId, job.has_glb);
        setStatus("Template ready! Download below.", 100);
        generateBtn.disabled = false;
      } else if (job.status === "failed") {
        clearInterval(pollTimer);
        hideDownloads();
        showError(job.error || "Pipeline failed");
        setStatus("Failed", 100);
        generateBtn.disabled = false;
      }
    } catch (e) {
      console.error(e);
    }
  }, 1500);
}
