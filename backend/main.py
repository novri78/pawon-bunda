from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ConfigDict
from openai import OpenAI
from typing import List, Dict, Literal, Optional
from fastapi.middleware.cors import CORSMiddleware


import os
import json
import logging
import uuid
import time
from collections import defaultdict, deque



# ============================================================
# LOGGING
# ============================================================


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler()]
)



logger = logging.getLogger("api")



# ============================================================
# APPLICATION
# ============================================================

APP_NAME = os.getenv("APP_NAME", "Pawon Bunda API")
APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen/qwen3.6-27b")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.6"))
MAX_COMPLETION_TOKENS = int(os.getenv("MAX_COMPLETION_TOKENS", "500"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
rate_limit_hits = defaultdict(deque)

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION
)

configured_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_origins,
    allow_credentials=False,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type", "X-Request-ID"]
)



# ============================================================
# API REQUEST LOGGING MIDDLEWARE
# ============================================================

@app.middleware("http")
async def log_requests(request: Request, call_next):

    start_time = time.perf_counter()
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = request_id

    logger.info(
        "request_started request_id=%s client=%s method=%s path=%s",
        request_id,
        request.client.host if request.client else "unknown",
        request.method,
        request.url.path
    )

    try:

        response = await call_next(request)

        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_finished request_id=%s method=%s path=%s status=%s duration_ms=%d",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            int((time.perf_counter() - start_time) * 1000)
        )

        return response

    except Exception as error:

        logger.exception(
            "request_failed request_id=%s method=%s path=%s duration_ms=%d",
            request_id,
            request.method,
            request.url.path,
            int((time.perf_counter() - start_time) * 1000)
        )

        raise


@app.middleware("http")
async def rate_limit_requests(request: Request, call_next):
    if request.url.path in {"/health", "/"}:
        return await call_next(request)

    client_ip = request.headers.get("X-Real-IP") or (
        request.client.host if request.client else "unknown"
    )
    now = time.monotonic()
    hits = rate_limit_hits[client_ip]

    while hits and now - hits[0] >= 60:
        hits.popleft()

    if len(hits) >= RATE_LIMIT_PER_MINUTE:
        return JSONResponse(
            status_code=429,
            content={"detail": "Terlalu banyak permintaan. Silakan coba lagi nanti."},
            headers={"Retry-After": "60"}
        )

    hits.append(now)
    return await call_next(request)


DEFAULT_SYSTEM_PROMPT = (
    "Anda adalah Asisten humanis AI untuk Pawon Bunda Ety Cileunyi."
    "Jawablah pertanyaan pelanggan dengan ramah, jelas, dan singkat (maksimal 2-3 paragraf pendek)."
    "Jangan memberikan jawaban yang terlalu panjang."
    # "You are a helpful, precise, professional AI assistant. "
    # "Answer naturally, humanistic, ignore potential AI phrase/name and clearly. "
    # "Do not expose internal reasoning, hidden chain-of-thought, "
    # "or <think> blocks. "
    # "Present the final answer in a human-friendly format. "
    # "Use headings, bullets, numbered steps, examples, and tables "
    # "when they improve clarity."
)


# ============================================================
# OPENAI-COMPATIBLE CLIENT
# ============================================================

client = None


def get_openai_client() -> OpenAI:
    global client

    if client is None:
        api_key = os.getenv("QWEN_API_KEY")

        if not api_key:
            raise RuntimeError(
                "QWEN_API_KEY is not configured in the environment."
            )

        client = OpenAI(
            api_key=api_key,
            base_url=GROQ_BASE_URL
        )

    return client


# ============================================================
# REQUEST MODEL
# ============================================================

class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, max_length=4000)

    history: List[ChatMessage] = Field(
        default_factory=list,
        max_length=12
    )

    system_prompt: Optional[str] = Field(default=None, max_length=1000)


# ============================================================
# HELPER
# ============================================================

def build_messages(
    request: ChatRequest
) -> List[Dict[str, str]]:

    system_prompt = DEFAULT_SYSTEM_PROMPT

    messages = [
        {
            "role": "system",
            "content": system_prompt
        }
    ]

    for message in request.history[-6:]:

        messages.append(
            {
                "role": message.role,
                "content": message.content
            }
        )

    messages.append(
        {
            "role": "user",
            "content": request.message
        }
    )

    return messages


# ============================================================
# STREAMING ENDPOINT
# ============================================================

@app.post("/chat/stream")
def chat_stream(request: Request, payload: ChatRequest):

    logger.info(
        "chat_started request_id=%s message_length=%d history=%d",
        getattr(request.state, "request_id", "unknown"),
        len(payload.message),
        len(payload.history)
    )

    messages = build_messages(payload)

    def generate():

        try:
            openai_client = get_openai_client()

            logger.info("provider_request request_id=%s model=%s", getattr(request.state, "request_id", "unknown"), MODEL_NAME)

            stream = openai_client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                stream=True,
                reasoning_effort="none",
                temperature=TEMPERATURE,
                max_completion_tokens=MAX_COMPLETION_TOKENS
            )

            for chunk in stream:

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                if not delta.content:
                    continue

                event = {
                    "type": "content",
                    "content": delta.content
                }

                yield (
                    f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                )

            logger.info("chat_completed request_id=%s model=%s", getattr(request.state, "request_id", "unknown"), MODEL_NAME)

            yield (
                f"data: {json.dumps({'type': 'done'})}\n\n"
            )

        except Exception as error:

            logger.exception("provider_failed request_id=%s", getattr(request.state, "request_id", "unknown"))

            error_event = {
                "type": "error",
                "message": "Asisten sedang mengalami gangguan. Silakan coba lagi atau lanjut melalui WhatsApp.",
                "request_id": getattr(request.state, "request_id", "unknown")
            }

            yield (
                f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health_check():

    return {
        "status": "ok",
        "version": APP_VERSION
    }
