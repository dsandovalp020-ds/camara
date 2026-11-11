"""Logica de cada mensaje: pausa, limite, aviso de privacidad, agente y comandos del administrador."""
import logging
import os
import re

import agent
import kb
import store
import voice

log = logging.getLogger("bot")
# WhatsApp envia "from" sin "+": se guardan solo los digitos para que "+57..." tambien coincida
ADMINS = {re.sub(r"\D", "", n) for n in os.getenv("ADMIN_NUMBERS", "").split(",") if re.sub(r"\D", "", n)}
CHAMBER = os.getenv("CHAMBER_NAME", "la Cámara de Comercio")
MAX_PER_HOUR = int(os.getenv("MAX_MSGS_PER_HOUR", "30"))

PRIVACY = os.getenv("PRIVACY_NOTICE") or (
    f"Ciao! Sono l'assistente virtuale (IA) di {CHAMBER}. Le conversazioni vengono conservate per migliorare "
    "il servizio; potete chiedere un operatore in qualsiasi momento.\n"
    f"¡Hola! Soy el asistente virtual (IA) de {CHAMBER}. Las conversaciones se guardan para mejorar el "
    "servicio; puede pedir hablar con una persona en cualquier momento.")
NO_TEXT = ("Por ahora solo puedo leer mensajes de texto y notas de voz. / Per ora posso leggere solo messaggi "
           "di testo e vocali. / Por enquanto só leio mensagens de texto e áudios.")
NO_AUDIO = ("No logré entender el audio, ¿puede repetirlo o escribirlo? / Non sono riuscito a capire il vocale, "
            "può ripeterlo o scriverlo? / Não consegui entender o áudio, pode repetir ou escrever?")
RATE_MSG = ("Ha enviado muchos mensajes en poco tiempo; intente de nuevo más tarde. / Troppi messaggi in poco "
            "tempo, riprovi più tardi.")
AYUDA = """Comandos de administrador:
/estado - resumen
/pendientes - chats con asesor
/chat N [k] - ultimos k mensajes (N = numero con codigo de pais)
/responder N texto - escribirle al cliente
/pausa N · /reanudar N
/recargar - recarga los documentos"""
ROLES = {"customer": "Cliente", "agent": "Agente", "human": "Asesor", "notice": "Aviso", "handoff": "Derivado"}


async def handle(msg: dict, wa) -> None:
    if not isinstance(msg, dict):
        log.warning("Evento entrante ignorado: el mensaje no es un objeto")
        return
    sender, kind = msg.get("from"), msg.get("type")
    if (not isinstance(sender, str) or not sender.isdigit() or not 5 <= len(sender) <= 20
            or not isinstance(kind, str) or not kind):
        log.warning("Evento entrante ignorado: faltan remitente o tipo válidos")
        return
    text_data = msg.get("text")
    body = text_data.get("body", "") if kind == "text" and isinstance(text_data, dict) else ""
    if not isinstance(body, str):
        body = ""
    try:
        if sender in ADMINS and body.startswith("/"):
            await _admin(sender, body, wa)
            return
        c = store.touch(sender)
        if kind == "audio":
            audio_data = msg.get("audio")
            media_id = audio_data.get("id") if isinstance(audio_data, dict) else None
            if not isinstance(media_id, str) or not media_id:
                log.warning("Nota de voz ignorada: falta el identificador del medio")
                if not c["paused"]:
                    await wa.send_text(sender, NO_AUDIO)
                return
            body = await voice.transcribe(await wa.download_media(media_id))
            if not body:
                store.log(sender, "customer", "[nota de voz sin voz reconocible]")
                if not c["paused"]:
                    await wa.send_text(sender, NO_AUDIO)
                return
            body = f"[Nota de voz, transcripción automática] {body}"
        elif kind != "text":
            store.log(sender, "customer", f"[mensaje de tipo {kind}]")
            if not c["paused"]:
                await wa.send_text(sender, NO_TEXT)
            return
        store.log(sender, "customer", body)
        if c["is_new"]:
            store.log(sender, "notice", PRIVACY)
            await wa.send_text(sender, PRIVACY)
        if c["paused"]:
            return  # lo atiende una persona
        if store.count_recent(sender) > MAX_PER_HOUR:
            if not c["warned"]:
                store.set_flag(sender, "warned", 1)
                await wa.send_text(sender, RATE_MSG)
            return
        if c["warned"]:
            store.set_flag(sender, "warned", 0)

        async def handoff(motivo: str, resumen: str) -> None:
            store.log(sender, "handoff", motivo)  # el historial recuerda que ya se derivo
            aviso = (f"🔔 Solicitud para Contanza, asistente de Nathalie Alvarado\n"
                     f"Cliente: +{sender}\nMotivo: {motivo}\nResumen: {resumen}\n\n"
                     f"/chat {sender} · /responder {sender} texto · /pausa {sender}")
            for a in ADMINS:
                try:
                    await wa.send_text(a, aviso)
                except Exception as e:
                    log.warning("No pude avisar a %s (¿ventana de 24 h cerrada?): %s", a, e)

        text = await agent.reply(store.history(sender), handoff) or \
            "Un asesor le ayudará con su consulta. / Un operatore la aiuterà con la sua richiesta."
        await wa.send_text(sender, text)
        store.log(sender, "agent", text)  # se guarda solo si WhatsApp acepto el envio
    except Exception:
        log.exception("Error procesando mensaje entrante")
        try:
            await wa.send_text(sender, "Ha ocurrido un problema, intente de nuevo en un momento. / "
                                       "Si è verificato un problema, riprovi tra poco.")
        except Exception:
            pass


async def _admin(admin: str, body: str, wa) -> None:
    cmd, _, rest = body.strip().partition(" ")
    cmd = cmd.lower()
    arg, _, extra = rest.strip().partition(" ")
    num = re.sub(r"\D", "", arg)
    send = lambda t: wa.send_text(admin, t)  # noqa: E731

    if cmd in ("/ayuda", "/help"):
        await send(AYUDA)
    elif cmd == "/estado":
        s = store.stats()
        await send(f"Contactos: {s['contactos']} · En pausa: {s['pausados']}\n"
                   f"Mensajes (24 h): {s['mensajes_24h']}\nDocumentos: ~{agent.reload():,} tokens")
    elif cmd == "/pendientes":
        p = store.contacts_where("paused")
        await send("Chats con asesor:\n" + "\n".join(f"+{x}" for x in p) if p else "No hay chats pendientes.")
    elif cmd == "/recargar":
        await send(f"Documentos recargados: ~{agent.reload():,} tokens.")
    elif cmd in ("/pausa", "/reanudar") and num:
        field, val = {"/pausa": ("paused", 1), "/reanudar": ("paused", 0)}[cmd]
        store.set_flag(num, field, val)
        await send(f"Hecho: {cmd} +{num}")
    elif cmd == "/chat" and num:
        k = int(extra) if extra.strip().isdigit() else 10
        rows = store.last_messages(num, min(k, 30))
        txt = "\n".join(f"{ROLES.get(r['role'], r['role'])}: {r['text']}" for r in rows) or "Sin mensajes."
        await send(txt[-3500:])
    elif cmd == "/responder" and num and extra.strip():
        try:
            await wa.send_text(num, extra.strip())
        except Exception as e:
            await send(f"No se pudo enviar (si el cliente escribió hace más de 24 h, Meta lo rechaza): {e}")
            return
        store.log(num, "human", extra.strip())
        await send(f"Enviado a +{num}. El agente sigue activo; usa /pausa {num} para pausarlo.")
    else:
        await send("Comando no reconocido.\n" + AYUDA)
