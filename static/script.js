const chatWindow = document.getElementById("chatWindow");
const chatForm = document.getElementById("chatForm");
const questionInput = document.getElementById("questionInput");
const docFilter = document.getElementById("docFilter");
const typingIndicator = document.getElementById("typingIndicator");
const themeToggle = document.getElementById("themeToggle");
const downloadBtn = document.getElementById("downloadBtn");

let sessionId = localStorage.getItem("sop_session_id") || null;
const transcript = [];

// Load the list of actually-indexed SOPs into the dropdown
fetch("/documents")
  .then(r => r.json())
  .then(names => {
    names.forEach(name => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      docFilter.appendChild(opt);
    });
  })
  .catch(() => {});

function addMessage(role, text, meta) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrap.appendChild(bubble);

  if (role === "assistant" && meta) {
    if (meta.confidence_label) {
      const badge = document.createElement("span");
      badge.className = `badge mt-1 badge-confidence-${meta.confidence_label}`;
      badge.textContent = `Confidence: ${meta.confidence_label} (${(meta.confidence * 100).toFixed(0)}%)`;
      wrap.appendChild(badge);
    }
    if (meta.sources && meta.sources.length) {
      const src = document.createElement("div");
      src.className = "sources";
      src.innerHTML = "<strong>Sources:</strong><br>" + meta.sources.map(s =>
        `• ${s.document} — ${s.header}${s.sub_header ? " > " + s.sub_header : ""}${s.page ? " (p." + s.page + ")" : ""}`
      ).join("<br>");
      wrap.appendChild(src);
    }
    const copyBtn = document.createElement("div");
    copyBtn.className = "copy-btn text-muted";
    copyBtn.textContent = "📋 Copy";
    copyBtn.onclick = () => navigator.clipboard.writeText(text);
    wrap.appendChild(copyBtn);
  }

  chatWindow.appendChild(wrap);
  chatWindow.scrollTop = chatWindow.scrollHeight;
  transcript.push({ role, text });
}

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = questionInput.value.trim();
  if (!question) return;

  addMessage("user", question + (docFilter.value ? `  [${docFilter.value} only]` : ""));
  questionInput.value = "";
  typingIndicator.classList.remove("d-none");

  try {
    const resp = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, session_id: sessionId, document_filter: docFilter.value }),
    });
    const data = await resp.json();
    sessionId = data.session_id;
    localStorage.setItem("sop_session_id", sessionId);

    addMessage("assistant", data.answer, data);
  } catch (err) {
    addMessage("assistant", "Something went wrong reaching the server. Is the Flask app running?");
  } finally {
    typingIndicator.classList.add("d-none");
  }
});

themeToggle.addEventListener("click", () => {
  const html = document.documentElement;
  const isDark = html.getAttribute("data-bs-theme") === "dark";
  html.setAttribute("data-bs-theme", isDark ? "light" : "dark");
  themeToggle.textContent = isDark ? "🌙 Dark Mode" : "☀️ Light Mode";
});

downloadBtn.addEventListener("click", () => {
  const lines = transcript.map(m => `${m.role.toUpperCase()}: ${m.text}`).join("\n\n");
  const blob = new Blob([lines], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "sop_chat_transcript.txt";
  a.click();
  URL.revokeObjectURL(url);
});
