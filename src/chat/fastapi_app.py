"""
J.A.R.V.I.S. Chat — FastAPI SSE front-end for the S.T.A.R.K. orchestrator.

Run:  uv run python src/chat/fastapi_app.py

Endpoints:
  GET  /          — minimal chat UI (vanilla HTML/JS)
  POST /chat      — SSE stream: text/event-stream, sends delta chunks
  DELETE /session — clear history for the current session
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
load_dotenv()

from src.config.logging_config import setup_logging

setup_logging()

import logfire

logfire.configure(service_name="stark-jarvis")
logfire.instrument_pydantic_ai()

import asyncio
import json
import logging
import uuid

import uvicorn
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from src.agents.jarvis_agent import agent as orchestrator, build_context_with_steps

logger = logging.getLogger("src.chat.fastapi_app")

app = FastAPI(title="J.A.R.V.I.S.")

# In-memory session store: session_id → list of pydantic-ai messages
# TTL of 1 hour, max 100 concurrent sessions
_sessions: TTLCache = TTLCache(maxsize=100, ttl=3600)

_SESSION_COOKIE = "jarvis_session"


def _get_or_create_session(request: Request) -> str:
    session_id = request.cookies.get(_SESSION_COOKIE)
    if not session_id or session_id not in _sessions:
        session_id = str(uuid.uuid4())
    return session_id


# ── Chat UI ────────────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>J.A.R.V.I.S.</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0d0f14;
    color: #e2e8f0;
    height: 100vh;
    display: flex;
    flex-direction: column;
  }

  #header {
    padding: 12px 20px;
    border-bottom: 1px solid #1e2433;
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
  }
  #header h1 { font-size: 1rem; font-weight: 600; letter-spacing: 0.05em; color: #7dd3fc; }
  #header span { font-size: 0.75rem; color: #64748b; }

  #messages {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    scroll-behavior: smooth;
  }

  .msg {
    max-width: 78%;
    padding: 10px 14px;
    border-radius: 12px;
    font-size: 0.9rem;
    line-height: 1.55;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .msg.user {
    align-self: flex-end;
    background: #1e3a5f;
    border-bottom-right-radius: 4px;
  }
  .msg.assistant {
    align-self: flex-start;
    background: #1a1f2e;
    border: 1px solid #1e2433;
    border-bottom-left-radius: 4px;
  }
  .msg.error { border-color: #7f1d1d; color: #fca5a5; }

  #input-area {
    border-top: 1px solid #1e2433;
    padding: 14px 20px;
    display: flex;
    gap: 10px;
    flex-shrink: 0;
    background: #0d0f14;
  }

  #user-input {
    flex: 1;
    background: #1a1f2e;
    border: 1px solid #1e2433;
    border-radius: 8px;
    color: #e2e8f0;
    padding: 10px 14px;
    font-size: 0.9rem;
    resize: none;
    outline: none;
    max-height: 140px;
    overflow-y: auto;
    transition: border-color 0.15s;
  }
  #user-input:focus { border-color: #3b82f6; }
  #user-input::placeholder { color: #475569; }

  #send-btn {
    background: #3b82f6;
    border: none;
    border-radius: 8px;
    color: #fff;
    padding: 0 18px;
    font-size: 0.9rem;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.15s;
    flex-shrink: 0;
  }
  #send-btn:hover { background: #2563eb; }
  #send-btn:disabled { background: #1e3a5f; color: #475569; cursor: not-allowed; }

  .thinking { color: #475569; font-style: italic; font-size: 0.8rem; }

  .think-panel {
    align-self: flex-start;
    max-width: 78%;
    background: #0d1117;
    border: 1px solid #1e2433;
    border-radius: 10px;
    overflow: hidden;
    font-size: 0.78rem;
  }
  .think-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 7px 12px;
    cursor: pointer;
    user-select: none;
    color: #64748b;
    border-bottom: 1px solid transparent;
    transition: border-color 0.2s;
  }
  .think-header.open { border-color: #1e2433; }
  .think-header .spinner {
    width: 10px; height: 10px;
    border: 2px solid #3b82f6;
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    flex-shrink: 0;
  }
  .think-header .spinner.done {
    border-color: #22c55e;
    border-top-color: #22c55e;
    animation: none;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .think-header .label { font-weight: 500; color: #94a3b8; }
  .think-header .chevron { margin-left: auto; transition: transform 0.2s; }
  .think-header.open .chevron { transform: rotate(180deg); }

  .think-steps {
    display: none;
    padding: 6px 12px 10px;
    flex-direction: column;
    gap: 4px;
  }
  .think-steps.open { display: flex; }
  .think-step {
    display: flex;
    gap: 8px;
    align-items: baseline;
    color: #475569;
  }
  .think-step .icon { color: #3b82f6; flex-shrink: 0; }
  .think-step .step-label { color: #94a3b8; }
  .think-step .step-detail { color: #475569; font-size: 0.72rem; }
</style>
</head>
<body>
<div id="header">
  <h1>J.A.R.V.I.S.</h1>
  <span>S.T.A.R.K. Athletic Intelligence</span>
</div>

<div id="messages"></div>

<div id="input-area">
  <textarea id="user-input" rows="1" placeholder="Ask about your training, biomechanics, readiness…"></textarea>
  <button id="send-btn">Send</button>
</div>

<script>
const messagesEl = document.getElementById("messages");
const inputEl    = document.getElementById("user-input");
const sendBtn    = document.getElementById("send-btn");

function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addMsg(role, text) {
  const el = document.createElement("div");
  el.className = "msg " + role;
  el.textContent = text;
  messagesEl.appendChild(el);
  scrollBottom();
  return el;
}

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;

  inputEl.value = "";
  inputEl.style.height = "auto";
  sendBtn.disabled = true;

  addMsg("user", text);

  // Thinking panel
  const thinkPanel = document.createElement("div");
  thinkPanel.className = "think-panel";
  thinkPanel.innerHTML = `
    <div class="think-header open" id="think-hdr">
      <div class="spinner" id="think-spinner"></div>
      <span class="label" id="think-label">Loading telemetry…</span>
      <span class="chevron">▾</span>
    </div>
    <div class="think-steps open" id="think-steps"></div>
  `;
  messagesEl.appendChild(thinkPanel);
  scrollBottom();

  const thinkHdr    = thinkPanel.querySelector("#think-hdr");
  const thinkLabel  = thinkPanel.querySelector("#think-label");
  const thinkSteps  = thinkPanel.querySelector("#think-steps");
  const thinkSpinner = thinkPanel.querySelector("#think-spinner");
  thinkHdr.addEventListener("click", () => {
    thinkHdr.classList.toggle("open");
    thinkSteps.classList.toggle("open");
  });

  const assistantEl = document.createElement("div");
  assistantEl.className = "msg assistant";
  assistantEl.style.display = "none";
  messagesEl.appendChild(assistantEl);

  function addStep(label, detail) {
    const row = document.createElement("div");
    row.className = "think-step";
    row.innerHTML = `<span class="icon">⚡</span><span class="step-label">${label}</span>`
      + (detail ? `<span class="step-detail">— ${detail}</span>` : "");
    thinkSteps.appendChild(row);
    thinkLabel.textContent = label;
    scrollBottom();
  }

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });

    if (!response.ok) {
      thinkPanel.remove();
      assistantEl.style.display = "";
      assistantEl.textContent = "Error: " + response.statusText;
      assistantEl.className = "msg assistant error";
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let responding = false;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6);
        if (payload === "[DONE]") break;
        try {
          const msg = JSON.parse(payload);
          if (msg.step !== undefined) {
            addStep(msg.step, msg.detail);
          } else if (msg.status === "thinking") {
            // heartbeat — panel already visible, nothing to do
          } else if (msg.delta !== undefined) {
            if (!responding) {
              // Collapse and dim the think panel, show response
              thinkHdr.classList.remove("open");
              thinkSteps.classList.remove("open");
              thinkSpinner.classList.add("done");
              thinkLabel.textContent = "Context loaded";
              assistantEl.style.display = "";
              responding = true;
            }
            assistantEl.textContent = msg.delta;
            scrollBottom();
          }
        } catch (_) {}
      }
    }

    if (!responding) {
      assistantEl.style.display = "";
      assistantEl.textContent = "(no response)";
    }

  } catch (err) {
    assistantEl.textContent = "Connection error: " + err.message;
    assistantEl.className = "msg assistant error";
  } finally {
    sendBtn.disabled = false;
    inputEl.focus();
    scrollBottom();
  }
}

sendBtn.addEventListener("click", sendMessage);

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Auto-resize textarea
inputEl.addEventListener("input", () => {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + "px";
});
</script>
</body>
</html>
"""


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(_HTML)


class ChatRequest(BaseModel):
    message: str


@app.post("/chat")
async def chat(body: ChatRequest, request: Request):
    user_message = body.message.strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="message is required")

    session_id = _get_or_create_session(request)
    history = _sessions.get(session_id, [])
    logger.info("session=%s | user: %s", session_id, user_message[:80])

    async def event_stream():
        is_first_message = len(history) == 0

        if is_first_message:
            # Load context in a thread to avoid blocking the event loop
            ctx_json = ""
            steps = await asyncio.to_thread(list, build_context_with_steps())
            for label, detail in steps:
                if label == "__context__":
                    ctx_json = detail
                else:
                    yield "data: " + json.dumps({"step": label, "detail": detail}) + "\n\n"
            prompt = f"[DATA CONTEXT]\n{ctx_json}\n[END DATA CONTEXT]\n\n{user_message}"
        else:
            prompt = user_message

        yield "data: " + json.dumps({"step": "Consulting J.A.R.V.I.S.…", "detail": None}) + "\n\n"
        try:
            async with orchestrator.run_stream(prompt, message_history=history) as result:
                async for text in result.stream_output(debounce_by=0.01):
                    yield "data: " + json.dumps({"delta": text}) + "\n\n"
            _sessions[session_id] = result.all_messages()
        except Exception as e:
            logger.error("Orchestrator error: %s", e, exc_info=True)
            yield "data: " + json.dumps({"delta": f"\n\n⚠️ Error: {e}"}) + "\n\n"

        yield "data: [DONE]\n\n"

    response = StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    response.set_cookie(_SESSION_COOKIE, session_id, samesite="lax", httponly=True, max_age=3600)
    return response


@app.delete("/session")
async def clear_session(request: Request):
    session_id = request.cookies.get(_SESSION_COOKIE)
    if session_id and session_id in _sessions:
        del _sessions[session_id]
    return {"cleared": True}


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "src.chat.fastapi_app:app",
        host="127.0.0.1",
        port=7934,
        reload=False,
    )