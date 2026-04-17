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

from src.config.logging_config import setup_logging

setup_logging()

import json
import logging
import uuid
from collections import defaultdict

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse

from src.agents.orchestrator import agent as orchestrator

logger = logging.getLogger("src.chat.fastapi_app")

app = FastAPI(title="J.A.R.V.I.S.")

# In-memory session store: session_id → list of pydantic-ai messages
_sessions: dict[str, list] = defaultdict(list)

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

  const assistantEl = addMsg("assistant thinking", "…");

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });

    if (!response.ok) {
      assistantEl.textContent = "Error: " + response.statusText;
      assistantEl.className = "msg assistant error";
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullText = "";
    let firstChunk = true;

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
          const { delta } = JSON.parse(payload);
          if (firstChunk) {
            assistantEl.textContent = "";
            assistantEl.className = "msg assistant";
            firstChunk = false;
          }
          fullText += delta;
          assistantEl.textContent = fullText;
          scrollBottom();
        } catch (_) {}
      }
    }

    if (firstChunk) {
      assistantEl.className = "msg assistant";
      assistantEl.textContent = fullText || "(no response)";
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


@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    user_message: str = body.get("message", "").strip()
    if not user_message:
        return Response(status_code=400, content="message is required")

    session_id = _get_or_create_session(request)
    history = _sessions[session_id]
    logger.info("session=%s | user: %s", session_id, user_message[:80])

    async def event_stream():
        new_history: list = []
        try:
            async with orchestrator.run_stream(
                user_message, message_history=history
            ) as result:
                async for chunk in result.stream_text(delta=True):
                    yield "data: " + json.dumps({"delta": chunk}) + "\n\n"
                new_history.extend(result.all_messages())
        except Exception as e:
            logger.error("Orchestrator error: %s", e, exc_info=True)
            yield "data: " + json.dumps({"delta": f"\n\n⚠️ Error: {e}"}) + "\n\n"

        yield "data: [DONE]\n\n"
        _sessions[session_id] = new_history

    response = StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    response.set_cookie(_SESSION_COOKIE, session_id, samesite="lax", httponly=True)
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