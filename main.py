"""Agente de WhatsApp de la Camara de Comercio.   Ejecutar:  uvicorn main:app --port 8001"""
import hashlib
import hmac
import json
import logging
import os
import threading

from dotenv import load_dotenv

load_dotenv()

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response  # noqa: E402

import bot  # noqa: E402
import store  # noqa: E402
import voice  # noqa: E402
from whatsapp import WhatsApp  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler("camara.log", encoding="utf-8")])
log = logging.getLogger("main")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
APP_SECRET = os.getenv("APP_SECRET", "")
MAX_WEBHOOK_BYTES = int(os.getenv("MAX_WEBHOOK_BYTES", str(1024 * 1024)))
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "90"))


def _parse_phone_ids(*env_names: str) -> tuple[str, ...]:
    ids: list[str] = []
    for env_name in env_names:
        for part in os.getenv(env_name, "").replace(";", ",").split(","):
            cleaned = part.strip()
            if cleaned and cleaned not in ids:
                ids.append(cleaned)
    return tuple(ids)


EXPECTED_PHONE_NUMBER_IDS = _parse_phone_ids("PHONE_NUMBER_ID", "PRIMARY_PHONE_NUMBER_ID")

app = FastAPI()
wa = WhatsApp()


@app.on_event("startup")
async def startup_event() -> None:
    if not VERIFY_TOKEN.strip():
        raise RuntimeError("VERIFY_TOKEN no configurado")
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET no configurado: se requiere para verificar la firma del webhook de Meta")
    if not bot.ADMINS:
        raise RuntimeError("ADMIN_NUMBERS no configurado: no hay administrador para recibir derivaciones")
    if MAX_WEBHOOK_BYTES <= 0 or RETENTION_DAYS <= 0:
        raise RuntimeError("MAX_WEBHOOK_BYTES y RETENTION_DAYS deben ser mayores que cero")
    await wa.validate_phone_access()
    store.purge(RETENTION_DAYS)
    threading.Thread(target=voice.load, daemon=True).start()  # el modelo de voz carga en segundo plano


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await wa.http.aclose()


@app.api_route("/", methods=["GET", "HEAD"])
async def root(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)
    return {"status": "ok", "service": "camara-agent", "webhook": "/webhook"}


@app.get("/webhook")
async def verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=q.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(status_code=403)


@app.post("/webhook")
async def incoming(request: Request, background: BackgroundTasks):
    body_parts = []
    body_size = 0
    async for part in request.stream():
        body_size += len(part)
        if body_size > MAX_WEBHOOK_BYTES:
            raise HTTPException(status_code=413, detail="Webhook payload too large")
        body_parts.append(part)
    raw = b"".join(body_parts)
    expected = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("X-Hub-Signature-256", ""), expected):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("Webhook JSON invalido; cuerpo omitido de logs")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        log.warning("Webhook ignorado: 'entry' no es una lista")
        return {"status": "ok"}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes", [])
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value", {}) or {}
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue
            phone_number_id = str(metadata.get("phone_number_id", "") or "")
            if EXPECTED_PHONE_NUMBER_IDS:
                if not phone_number_id:
                    log.warning(
                        "Webhook rechazado: metadata.phone_number_id ausente; se esperaba %s",
                        ", ".join(EXPECTED_PHONE_NUMBER_IDS),
                    )
                    continue
                if phone_number_id not in EXPECTED_PHONE_NUMBER_IDS:
                    log.warning(
                        "Webhook rechazado: phone_number_id=%s no coincide con %s",
                        phone_number_id,
                        ", ".join(EXPECTED_PHONE_NUMBER_IDS),
                    )
                    continue
            messages = value.get("messages", []) or []
            if not isinstance(messages, list):
                log.warning("Webhook ignorado: 'messages' no es una lista")
                continue
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                msg_id = msg.get("id")
                sender = msg.get("from")
                kind = msg.get("type")
                if (not isinstance(msg_id, str) or not msg_id or len(msg_id) > 256
                        or not isinstance(sender, str) or not sender.isdigit()
                        or not 5 <= len(sender) <= 20
                        or not isinstance(kind, str) or not kind):
                    log.warning("Webhook ignorado: mensaje sin id, remitente o tipo válidos")
                    continue
                if store.already_seen(msg_id):
                    continue
                background.add_task(bot.handle, msg, wa)
    return {"status": "ok"}
