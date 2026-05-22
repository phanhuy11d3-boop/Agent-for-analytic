const textarea       = document.getElementById("question-input");
const btnAnalyze     = document.getElementById("btn-analyze");
const reportFrame    = document.getElementById("report-frame");
const placeholder    = document.getElementById("placeholder");
const reportLink     = document.getElementById("report-link");
const datasetSelect  = document.getElementById("dataset-select");
const uploadDrop     = document.getElementById("upload-drop");
const fileInput      = document.getElementById("file-input");
const uploadLabel    = document.getElementById("upload-label");
const uploadStatus   = document.getElementById("upload-status");
const btnRefresh     = document.getElementById("btn-refresh-tables");

const AGENTS = ["data_agent", "analytics_agent", "critic_agent", "writer_agent"];

const AGENT_META = {
  data_agent:      { icon: "🗄️", label: "Data Agent" },
  analytics_agent: { icon: "📊", label: "Analytics Agent" },
  critic_agent:    { icon: "🔍", label: "Critic Agent" },
  writer_agent:    { icon: "✍️",  label: "Writer Agent" },
};

function resetAgents() {
  AGENTS.forEach(id => {
    const row = document.getElementById("agent-" + id);
    if (!row) return;
    row.className = "agent-row";
    const msgEl = row.querySelector(".agent-msg");
    if (msgEl) msgEl.textContent = "";
  });
}

function setAgentState(nodeId, state, message) {
  const row = document.getElementById("agent-" + nodeId);
  if (!row) return;
  row.className = "agent-row " + state;
  const msgEl = row.querySelector(".agent-msg");
  if (msgEl) msgEl.textContent = message || "";
}

function showReport(url) {
  placeholder.style.display  = "none";
  reportFrame.style.display  = "block";
  reportFrame.src = url;
  reportLink.href = url;
  reportLink.style.display = "inline";
}

function analyze() {
  const question = textarea.value.trim();
  if (!question) return;

  btnAnalyze.disabled = true;
  btnAnalyze.textContent = "⏳ Analyzing…";
  placeholder.style.display = "flex";
  reportFrame.style.display = "none";
  reportLink.style.display  = "none";
  resetAgents();

  const ws = new WebSocket(`ws://${location.host}/ws/analyze`);

  ws.onopen = () => {
    const selectedTable = datasetSelect ? (datasetSelect.value || null) : null;
    ws.send(JSON.stringify({ question, selected_table: selectedTable }));
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    if (msg.type === "start") {
      placeholder.querySelector("p").textContent = "Agents are working…";
    }

    if (msg.type === "step") {
      const step = msg.step || {};
      const agentId = msg.agent;
      const status  = step.status === "error" ? "error" : "success";

      // Mark previous agents as success if needed
      const idx = AGENTS.indexOf(agentId);
      for (let i = 0; i < idx; i++) {
        const row = document.getElementById("agent-" + AGENTS[i]);
        if (row && !row.classList.contains("success") && !row.classList.contains("error")) {
          setAgentState(AGENTS[i], "success", "Done");
        }
      }
      setAgentState(agentId, status, step.message || "");
    }

    if (msg.type === "complete") {
      btnAnalyze.disabled = false;
      btnAnalyze.textContent = "✨ Analyze";

      // Mark all agents done
      AGENTS.forEach(id => {
        const row = document.getElementById("agent-" + id);
        if (row && !row.classList.contains("error")) {
          setAgentState(id, "success", "Done");
        }
      });

      if (msg.report_url) {
        showReport(msg.report_url);
      } else if (msg.data_error) {
        placeholder.querySelector("p").textContent = "Error: " + msg.data_error;
      }
    }

    if (msg.type === "error") {
      btnAnalyze.disabled = false;
      btnAnalyze.textContent = "✨ Analyze";
      placeholder.querySelector("p").textContent = "Error: " + msg.message;
    }
  };

  ws.onerror = () => {
    btnAnalyze.disabled = false;
    btnAnalyze.textContent = "✨ Analyze";
    placeholder.querySelector("p").textContent = "Connection error — is the server running?";
  };
}

// Button click
btnAnalyze.addEventListener("click", analyze);

// Ctrl+Enter to submit
textarea.addEventListener("keydown", e => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) analyze();
});

// Suggested questions
document.querySelectorAll(".suggestion-chip").forEach(chip => {
  chip.addEventListener("click", () => {
    textarea.value = chip.textContent.trim();
    analyze();
  });
});

// ── Dataset / Upload ────────────────────────────────────────────────────────

async function loadTables() {
  try {
    const resp = await fetch("/api/tables");
    if (!resp.ok) return;
    const { tables } = await resp.json();
    const current = datasetSelect.value;
    while (datasetSelect.options.length > 1) datasetSelect.remove(1);
    for (const t of tables) {
      const opt = document.createElement("option");
      opt.value = t.name;
      const tag = t.is_uploaded ? "[Uploaded] " : "";
      opt.textContent = `${tag}${t.name} (${t.row_count.toLocaleString()} rows)`;
      datasetSelect.appendChild(opt);
    }
    if (current) datasetSelect.value = current;
  } catch (_) {}
}

async function handleUpload(file) {
  uploadStatus.style.display = "block";
  uploadStatus.className = "upload-status uploading";
  uploadLabel.textContent = file.name;

  // Live elapsed timer so user knows upload is in progress
  const startTs = Date.now();
  let timerInterval = setInterval(() => {
    const sec = Math.round((Date.now() - startTs) / 1000);
    uploadStatus.textContent = `Uploading… ${sec}s (large files may take up to 60s)`;
  }, 1000);
  uploadStatus.textContent = "Uploading…";

  const form = new FormData();
  form.append("file", file);

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 120_000);

  try {
    const resp = await fetch("/api/upload", {
      method: "POST", body: form, signal: controller.signal,
    });
    clearTimeout(timeoutId);
    clearInterval(timerInterval);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Upload failed");
    uploadStatus.className = "upload-status success";
    uploadStatus.textContent = data.message;

    // Add new table to dropdown immediately from response — avoids slow COUNT(*) roundtrip
    const existing = Array.from(datasetSelect.options).find(o => o.value === data.table_name);
    if (!existing) {
      const opt = document.createElement("option");
      opt.value = data.table_name;
      opt.textContent = `[Uploaded] ${data.table_name} (${data.rows.toLocaleString()} rows)`;
      datasetSelect.appendChild(opt);
    }
    datasetSelect.value = data.table_name;
    loadTables(); // refresh full list in background, no await
  } catch (err) {
    clearTimeout(timeoutId);
    clearInterval(timerInterval);
    uploadStatus.className = "upload-status error";
    uploadStatus.textContent = err.name === "AbortError"
      ? "Upload timed out — file may be too large or server is busy"
      : "Error: " + err.message;
    uploadLabel.textContent = "Drop file or click to browse";
  }
}

// Click to browse
uploadDrop.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) handleUpload(fileInput.files[0]);
});

// Drag and drop
uploadDrop.addEventListener("dragover", e => {
  e.preventDefault();
  uploadDrop.classList.add("drag-over");
});
uploadDrop.addEventListener("dragleave", () => uploadDrop.classList.remove("drag-over"));
uploadDrop.addEventListener("drop", e => {
  e.preventDefault();
  uploadDrop.classList.remove("drag-over");
  const file = e.dataTransfer.files[0];
  if (file) handleUpload(file);
});

// Refresh button
btnRefresh.addEventListener("click", loadTables);

// Load tables on startup
loadTables();
