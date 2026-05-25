function renderHeroSection() {
  const mount = document.getElementById("hero-section");
  if (!mount) return;
  if (mount.children.length) return;
  mount.innerHTML = `
    <h1 class="hero-title">
      AI for serious data work, unlock <mark>100x efficiency</mark>
    </h1>
    <p class="hero-copy">Built for teams that need trustworthy, fast data decisions.</p>
  `;
}

function FeatureCard(card) {
  if (card.variant === "promo") {
    return `
      <article class="feature-card promo-card">
        <span class="promo-badge">New!</span>
        <h2 class="promo-kicker">Meet<br>Maxxem <em>Bloom</em></h2>
        <div class="agent-orbs" aria-hidden="true">
          <span class="agent-orb orb-a"></span>
          <span class="agent-orb orb-b"></span>
          <span class="agent-orb orb-c"></span>
          <span class="agent-orb orb-d"></span>
          <span class="agent-orb orb-e"></span>
        </div>
        <p class="promo-subtitle">Your AI Data Agents Team!</p>
        <button class="promo-button feature-action" type="button">Launch in 1 Click &gt;</button>
      </article>
    `;
  }

  if (card.variant === "report") {
    return `
      <article class="feature-card report-generator-card">
        <h3 class="feature-title"><span class="feature-icon">${card.icon}</span>${card.title}</h3>
        <p class="feature-desc">${card.description}</p>
        <div class="feature-actions">
          <button class="feature-action primary" type="button" data-question="${card.demoQuestion}">Try our demo</button>
          <button class="feature-action secondary" type="button" data-open-workspace>Get started</button>
        </div>
        <div class="chart-preview" aria-hidden="true">
          <div class="chart-labels"><span></span><span></span><span></span></div>
          <div class="chart-bars"><span style="height:35%"></span><span style="height:70%"></span><span style="height:48%"></span><span style="height:86%"></span><span style="height:60%"></span></div>
        </div>
      </article>
    `;
  }

  const variant = card.variant === "processing" ? "processing-card" : "mini-card";
  return `
    <article class="feature-card ${variant}">
      <h3 class="feature-title"><span class="feature-icon">${card.icon}</span>${card.title}</h3>
      ${card.description ? `<p class="feature-desc">${card.description}</p>` : ""}
      <button class="feature-action" type="button" data-question="${card.question}">${card.ctaLabel} &gt;</button>
    </article>
  `;
}

function renderFeatureCards() {
  const mount = document.getElementById("feature-grid");
  if (!mount) return;
  if (mount.children.length) return;
  const cards = [
    { variant: "promo" },
    {
      variant: "report",
      icon: "AI",
      title: "AI Data Report Generator",
      description: "Upload your files, click, and your insightful visuals will be ready in minutes.",
      demoQuestion: "Summarize the dataset and key fields",
    },
    {
      variant: "processing",
      icon: "DP",
      title: "Data Processing Helper",
      description: "",
      ctaLabel: "Get started",
      question: "Find missing data patterns and possible issues",
    },
    {
      icon: "DV",
      title: "Data Visualization",
      ctaLabel: "Get started",
      question: "Compare averages across important segments",
    },
    {
      icon: "TF",
      title: "Trend Forecasting",
      ctaLabel: "Get started",
      question: "Highlight notable trends and changes over time",
    },
    {
      icon: "DC",
      title: "Data Cleaner",
      ctaLabel: "Get started",
      question: "Find missing data patterns and possible issues",
    },
  ];
  mount.innerHTML = cards.map(FeatureCard).join("");
}

renderHeroSection();
renderFeatureCards();

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
const semanticStatus = document.getElementById("semantic-status");
const btnSaveSemantic = document.getElementById("btn-save-semantic");
const btnSaveSemanticBottom = document.getElementById("btn-save-semantic-bottom");
const btnAnalyzeContext = document.getElementById("btn-analyze-context");
const semanticPurpose = document.getElementById("semantic-purpose");
const semanticGrain = document.getElementById("semantic-grain");
const semanticOutcome = document.getElementById("semantic-outcome");
const semanticPositive = document.getElementById("semantic-positive");
const semanticMetric = document.getElementById("semantic-metric");
const semanticColumns = document.getElementById("semantic-columns");
const semanticProposal = document.getElementById("semantic-proposal");
const btnApplySemanticProposal = document.getElementById("btn-apply-semantic-proposal");
const homeView = document.getElementById("home-view");
const reportView = document.getElementById("report-view");
const workspacePanel = document.getElementById("workspace-panel");
const workspaceBackdrop = document.getElementById("workspace-backdrop");
const btnDatasetsPanel = document.getElementById("btn-datasets-panel");
const btnCloseWorkspace = document.getElementById("btn-close-workspace");
const btnNewChat = document.getElementById("btn-new-chat");
const btnDiscover = document.getElementById("btn-discover");
const btnReportHome = document.getElementById("btn-report-home");
const btnHistoryToggle = document.getElementById("btn-history-toggle");

const AGENTS = ["semantic_agent", "data_agent", "analytics_agent", "critic_agent", "writer_agent"];

const AGENT_META = {
  semantic_agent:  { icon: "CTX", label: "Semantic Agent" },
  data_agent:      { icon: "🗄️", label: "Data Agent" },
  analytics_agent: { icon: "📊", label: "Analytics Agent" },
  critic_agent:    { icon: "🔍", label: "Critic Agent" },
  writer_agent:    { icon: "✍️",  label: "Writer Agent" },
};

let isUploading = false;
let isLoadingTables = false;
let isAnalyzing = false;
let isLoadingSemantic = false;
let semanticEditVersion = 0;
let currentProposal = null;

const semanticFields = [
  semanticPurpose,
  semanticGrain,
  semanticOutcome,
  semanticPositive,
  semanticMetric,
].filter(Boolean);

function updateUIState() {
  const disabled = isUploading || isLoadingTables || isAnalyzing || isLoadingSemantic;
  
  if (btnAnalyze) {
    btnAnalyze.disabled = disabled;
    if (isAnalyzing) {
      btnAnalyze.textContent = "⏳ Analyzing…";
    } else if (isUploading) {
      btnAnalyze.textContent = "⏳ Uploading…";
    } else if (isLoadingTables) {
      btnAnalyze.textContent = "⏳ Loading…";
    } else {
      btnAnalyze.textContent = "✨ Analyze";
    }
  }
  
  if (btnAnalyze) {
    if (isAnalyzing) {
      btnAnalyze.textContent = "Analyzing...";
    } else if (isUploading) {
      btnAnalyze.textContent = "Uploading...";
    } else if (isLoadingTables) {
      btnAnalyze.textContent = "Loading...";
    } else {
      btnAnalyze.textContent = "Analyze";
    }
  }

  if (btnAnalyzeContext) {
    btnAnalyzeContext.disabled = disabled;
    if (isAnalyzing) {
      btnAnalyzeContext.textContent = "Analyzing...";
    } else if (isUploading) {
      btnAnalyzeContext.textContent = "Uploading...";
    } else if (isLoadingTables) {
      btnAnalyzeContext.textContent = "Loading...";
    } else {
      btnAnalyzeContext.textContent = "Run Analyze";
    }
  }

  if (textarea) textarea.disabled = disabled;
  if (datasetSelect) datasetSelect.disabled = disabled;
  if (btnRefresh) btnRefresh.disabled = disabled;
  if (btnSaveSemantic) btnSaveSemantic.disabled = disabled || !datasetSelect.value;
  if (btnSaveSemanticBottom) btnSaveSemanticBottom.disabled = disabled || !datasetSelect.value;
  if (btnApplySemanticProposal) btnApplySemanticProposal.disabled = disabled || !currentProposal;
  
  document.querySelectorAll(".suggestion-chip").forEach(chip => {
    chip.disabled = disabled;
    if (disabled) {
      chip.style.opacity = "0.5";
      chip.style.pointerEvents = "none";
    } else {
      chip.style.opacity = "1";
      chip.style.pointerEvents = "auto";
    }
  });

  if (uploadDrop) {
    if (disabled) {
      uploadDrop.style.opacity = "0.5";
      uploadDrop.style.pointerEvents = "none";
    } else {
      uploadDrop.style.opacity = "1";
      uploadDrop.style.pointerEvents = "auto";
    }
  }
}

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

function showHomeView(message) {
  if (homeView) homeView.style.display = "flex";
  if (reportView) reportView.style.display = "none";
  if (reportFrame) reportFrame.style.display = "none";
  if (reportLink) reportLink.style.display = "none";
  if (placeholder) {
    placeholder.style.display = "flex";
    const statusText = placeholder.querySelector("p");
    if (statusText && message) statusText.textContent = message;
  }
}

function showReport(url) {
  if (homeView) homeView.style.display = "none";
  if (reportView) reportView.style.display = "flex";
  if (placeholder) placeholder.style.display = "none";
  if (reportFrame) {
    reportFrame.style.display = "block";
    reportFrame.src = url;
  }
  if (reportLink) {
    reportLink.href = url;
    reportLink.style.display = "inline";
  }
}

function openWorkspacePanel() {
  if (!workspacePanel || !workspaceBackdrop) return;
  workspacePanel.classList.add("open");
  workspacePanel.setAttribute("aria-hidden", "false");
  workspaceBackdrop.hidden = false;
}

function closeWorkspacePanel() {
  if (!workspacePanel || !workspaceBackdrop) return;
  workspacePanel.classList.remove("open");
  workspacePanel.setAttribute("aria-hidden", "true");
  workspaceBackdrop.hidden = true;
}

function setPlaceholderMessage(message) {
  const statusText = placeholder ? placeholder.querySelector("p") : null;
  if (statusText) statusText.textContent = message;
}

function setSemanticStatus(message, mode) {
  if (!semanticStatus) return;
  semanticStatus.textContent = message;
  semanticStatus.className = "semantic-status" + (mode ? " " + mode : "");
}

function fillSemanticColumns(mschema) {
  if (!semanticColumns) return;
  semanticColumns.innerHTML = "";
  const columns = (mschema && mschema.columns) || [];
  columns.forEach(col => {
    const opt = document.createElement("option");
    opt.value = col.name;
    semanticColumns.appendChild(opt);
  });
}

function fillSemanticForm(context) {
  if (!context) context = {};
  if (semanticPurpose) semanticPurpose.value = context.table_purpose || "";
  if (semanticGrain) semanticGrain.value = context.row_grain || "";
  if (semanticOutcome) semanticOutcome.value = context.outcome_column || "";
  if (semanticPositive) semanticPositive.value = context.positive_outcome_value || "";
  if (semanticMetric) semanticMetric.value = context.primary_metric || "";
}

function currentSemanticContext() {
  return {
    table_purpose: semanticPurpose ? semanticPurpose.value.trim() : "",
    row_grain: semanticGrain ? semanticGrain.value.trim() : "",
    outcome_column: semanticOutcome ? semanticOutcome.value.trim() : "",
    positive_outcome_value: semanticPositive ? semanticPositive.value.trim() : "",
    primary_metric: semanticMetric ? semanticMetric.value.trim() : "",
    confirmed: true,
    confirmation_source: "user",
  };
}

function clearSemanticProposal() {
  currentProposal = null;
  if (semanticProposal) {
    semanticProposal.hidden = true;
    semanticProposal.replaceChildren();
  }
  if (btnApplySemanticProposal) btnApplySemanticProposal.hidden = true;
}

function addProposalRow(root, label, value, confidence) {
  if (!value) return;
  const row = document.createElement("div");
  row.className = "proposal-row";
  const labelEl = document.createElement("div");
  labelEl.className = "proposal-label";
  labelEl.textContent = label;
  const valueEl = document.createElement("div");
  valueEl.className = "proposal-value";
  valueEl.textContent = value;
  const confidenceEl = document.createElement("div");
  confidenceEl.className = "proposal-confidence";
  confidenceEl.textContent = confidence !== undefined && confidence !== null
    ? `Confidence ${confidence}`
    : "Needs confirmation";
  row.append(labelEl, valueEl, confidenceEl);
  root.appendChild(row);
}

function renderSemanticProposal(proposal) {
  clearSemanticProposal();
  const context = proposal && proposal.context;
  if (!semanticProposal || !context) return;
  const confidence = proposal.confidence || {};
  addProposalRow(semanticProposal, "Table purpose", context.table_purpose, confidence.table_purpose);
  addProposalRow(semanticProposal, "Row grain", context.row_grain, confidence.row_grain);
  addProposalRow(semanticProposal, "Outcome column", context.outcome_column, confidence.outcome_column);
  addProposalRow(semanticProposal, "Positive outcome value", context.positive_outcome_value, confidence.positive_outcome_value);
  addProposalRow(semanticProposal, "Primary metric", context.primary_metric, confidence.primary_metric);
  if (!semanticProposal.children.length) return;
  currentProposal = proposal;
  semanticProposal.hidden = false;
  if (btnApplySemanticProposal) btnApplySemanticProposal.hidden = false;
}

async function applySemanticProposal() {
  const context = currentProposal && currentProposal.context;
  if (!context || !datasetSelect || !datasetSelect.value) return;
  const payload = {
    table_purpose: context.table_purpose || "",
    row_grain: context.row_grain || "",
    outcome_column: context.outcome_column || "",
    positive_outcome_value: context.positive_outcome_value || "",
    negative_outcome_value: context.negative_outcome_value || "",
    primary_metric: context.primary_metric || "",
    column_descriptions: context.column_descriptions || {},
    confirmed: true,
    confirmation_source: "proposal_confirm_button",
  };
  isLoadingSemantic = true;
  updateUIState();
  setSemanticStatus("Confirming semantic context...", "");
  try {
    const resp = await fetch("/api/semantic-context", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        table_name: datasetSelect.value,
        context: payload,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Could not confirm semantic context");
    fillSemanticForm(data.context || {});
    fillSemanticColumns(data.mschema || {});
    clearSemanticProposal();
    semanticEditVersion += 1;
    setSemanticStatus("Semantic layer confirmed. Run Analyze again for grounded output.", "ready");
  } catch (err) {
    setSemanticStatus("Confirm failed: " + err.message, "needs-context");
  } finally {
    isLoadingSemantic = false;
    updateUIState();
  }
}

async function loadSemanticContext() {
  if (!datasetSelect || !datasetSelect.value) {
    fillSemanticForm({});
    fillSemanticColumns({});
    clearSemanticProposal();
    semanticEditVersion += 1;
    setSemanticStatus("Select a table to load semantic context.", "");
    updateUIState();
    return;
  }
  if (isLoadingSemantic) return;
  const requestedTable = datasetSelect.value;
  const requestedEditVersion = semanticEditVersion;
  isLoadingSemantic = true;
  updateUIState();
  setSemanticStatus("Loading semantic context...", "");
  try {
    const params = new URLSearchParams({
      table: datasetSelect.value,
      question: textarea ? textarea.value.trim() : "",
      llm: "true",
    });
    const resp = await fetch(`/api/semantic-context?${params.toString()}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Could not load semantic context");
    if (datasetSelect.value === requestedTable && semanticEditVersion === requestedEditVersion) {
      fillSemanticForm(data.context || {});
    }
    fillSemanticColumns(data.mschema || {});
    renderSemanticProposal(data.proposal || null);
    const required = (data.gaps || []).filter(g => g.required).length;
    if (required) {
      setSemanticStatus(`${required} required context field(s) missing. Review the suggestions, then save to confirm.`, "needs-context");
    } else {
      setSemanticStatus("Semantic context is sufficient for the current question.", "ready");
    }
  } catch (err) {
    clearSemanticProposal();
    setSemanticStatus("Semantic context unavailable: " + err.message, "needs-context");
  } finally {
    isLoadingSemantic = false;
    updateUIState();
  }
}

async function saveSemanticContext() {
  if (!datasetSelect || !datasetSelect.value) return;
  const contextPayload = currentSemanticContext();
  isLoadingSemantic = true;
  updateUIState();
  setSemanticStatus("Saving semantic context...", "");
  try {
    const resp = await fetch("/api/semantic-context", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        table_name: datasetSelect.value,
        context: contextPayload,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Could not save semantic context");
    fillSemanticForm(data.context || {});
    fillSemanticColumns(data.mschema || {});
    setSemanticStatus("Semantic context saved. Run Analyze again to generate a new report.", "ready");
  } catch (err) {
    setSemanticStatus("Save failed: " + err.message, "needs-context");
  } finally {
    isLoadingSemantic = false;
    updateUIState();
  }
}

function analyze() {
  const question = textarea ? textarea.value.trim() : "";
  if (!question) return;

  isAnalyzing = true;
  updateUIState();
  showHomeView("Agents are working...");
  resetAgents();

  const ws = new WebSocket(`ws://${location.host}/ws/analyze`);
  let analysisFinished = false;

  ws.onopen = () => {
    const selectedTable = datasetSelect ? (datasetSelect.value || null) : null;
    ws.send(JSON.stringify({ question, selected_table: selectedTable }));
  };

  ws.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (err) {
      analysisFinished = true;
      isAnalyzing = false;
      updateUIState();
      setPlaceholderMessage("Error: server sent an unreadable response.");
      return;
    }

    if (msg.type === "start") {
      placeholder.querySelector("p").textContent = "Agents are working…";
    }

    if (msg.type === "heartbeat" && msg.message) {
      setPlaceholderMessage(msg.message);
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
      analysisFinished = true;
      isAnalyzing = false;
      updateUIState();

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
      analysisFinished = true;
      isAnalyzing = false;
      updateUIState();
      setPlaceholderMessage("Error: " + msg.message);
    }
  };

  ws.onerror = () => {
    analysisFinished = true;
    isAnalyzing = false;
    updateUIState();
    placeholder.querySelector("p").textContent = "Connection error — is the server running?";
  };
  ws.onclose = () => {
    if (!analysisFinished && isAnalyzing) {
      isAnalyzing = false;
      updateUIState();
      setPlaceholderMessage("Analysis connection closed before completion. Please retry.");
    }
  };
}

// Button click
if (btnAnalyze) btnAnalyze.addEventListener("click", analyze);
if (btnAnalyzeContext) btnAnalyzeContext.addEventListener("click", analyze);

// Ctrl+Enter to submit
if (textarea) {
  textarea.addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) analyze();
  });
}

// Suggested questions
document.querySelectorAll(".suggestion-chip").forEach(chip => {
  chip.addEventListener("click", () => {
    if (!textarea) return;
    textarea.value = chip.textContent.trim();
    closeWorkspacePanel();
    analyze();
  });
});

document.querySelectorAll(".feature-action[data-question]").forEach(button => {
  button.addEventListener("click", () => {
    if (!textarea) return;
    textarea.value = button.dataset.question || "";
    if (button.classList.contains("primary")) {
      analyze();
    } else {
      openWorkspacePanel();
    }
  });
});

document.querySelectorAll("[data-open-workspace]").forEach(button => {
  button.addEventListener("click", openWorkspacePanel);
});

document.querySelectorAll(".mode-pill").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".mode-pill").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
  });
});

if (btnDatasetsPanel) btnDatasetsPanel.addEventListener("click", openWorkspacePanel);
if (btnCloseWorkspace) btnCloseWorkspace.addEventListener("click", closeWorkspacePanel);
if (workspaceBackdrop) workspaceBackdrop.addEventListener("click", closeWorkspacePanel);
if (btnDiscover && homeView) btnDiscover.addEventListener("click", () => homeView.scrollTo({ top: 0, behavior: "smooth" }));
if (btnReportHome) btnReportHome.addEventListener("click", () => showHomeView("Ask a question to generate a report"));
if (btnNewChat) {
  btnNewChat.addEventListener("click", () => {
    if (textarea) textarea.value = "";
    resetAgents();
    closeWorkspacePanel();
    showHomeView("Ask a question to generate a report");
  });
}
if (btnHistoryToggle) {
  btnHistoryToggle.addEventListener("click", () => {
    btnHistoryToggle.classList.toggle("collapsed");
  });
}

// ── Dataset / Upload ────────────────────────────────────────────────────────

async function loadTables() {
  if (isLoadingTables) return;
  isLoadingTables = true;
  updateUIState();
  
  // Show a temporary loading option in dropdown
  const current = datasetSelect.value;
  while (datasetSelect.options.length > 1) datasetSelect.remove(1);
  const loadingOpt = document.createElement("option");
  loadingOpt.value = "";
  loadingOpt.textContent = "⏳ Loading datasets...";
  loadingOpt.disabled = true;
  datasetSelect.appendChild(loadingOpt);

  try {
    const resp = await fetch("/api/tables");
    if (!resp.ok) throw new Error();
    const { tables } = await resp.json();
    
    // Remove the loading option
    while (datasetSelect.options.length > 1) datasetSelect.remove(1);

    for (const t of tables) {
      const opt = document.createElement("option");
      opt.value = t.name;
      const tag = t.is_uploaded ? "[Uploaded] " : "";
      opt.textContent = `${tag}${t.name} (${t.row_count.toLocaleString()} rows)`;
      datasetSelect.appendChild(opt);
    }
    if (current) datasetSelect.value = current;
  } catch (_) {
    // If it fails, restore dropdown state
    while (datasetSelect.options.length > 1) datasetSelect.remove(1);
  } finally {
    isLoadingTables = false;
    updateUIState();
    loadSemanticContext();
  }
}

async function handleUpload(file) {
  isUploading = true;
  updateUIState();
  
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
    const contentType = resp.headers.get("content-type") || "";
    const data = contentType.includes("application/json")
      ? await resp.json()
      : { detail: await resp.text() };
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
  } finally {
    isUploading = false;
    updateUIState();
  }
}

// Click to browse
if (uploadDrop && fileInput) {
  uploadDrop.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) handleUpload(fileInput.files[0]);
  });
}

// Drag and drop
if (uploadDrop) {
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
}

// Refresh button
if (btnRefresh) btnRefresh.addEventListener("click", loadTables);
if (datasetSelect) datasetSelect.addEventListener("change", loadSemanticContext);
if (btnSaveSemantic) btnSaveSemantic.addEventListener("click", saveSemanticContext);
if (btnSaveSemanticBottom) btnSaveSemanticBottom.addEventListener("click", saveSemanticContext);
if (btnApplySemanticProposal) btnApplySemanticProposal.addEventListener("click", applySemanticProposal);
semanticFields.forEach(field => {
  field.addEventListener("input", () => {
    semanticEditVersion += 1;
  });
});
if (textarea) textarea.addEventListener("blur", loadSemanticContext);

// Load tables on startup
if (datasetSelect) loadTables();
