"""Cliente minimo de la WhatsApp Business Cloud API (Meta)."""
import os
from urllib.parse import urlparse

import httpx


class WhatsApp:
    def __init__(self) -> None:
        self.token = os.environ["WHATSAPP_TOKEN"]
        primary_phone_id = os.getenv("PRIMARY_PHONE_NUMBER_ID", "").strip()
        if not primary_phone_id:
            raw_phone_ids = os.getenv("PHONE_NUMBER_ID", "").replace(";", ",")
            primary_phone_id = next((p.strip() for p in raw_phone_ids.split(",") if p.strip()), "")
        self.phone_id = primary_phone_id
        if not self.phone_id:
            raise RuntimeError("PHONE_NUMBER_ID / PRIMARY_PHONE_NUMBER_ID no configurado o vacío")
        version = os.getenv("GRAPH_VERSION", "v21.0")
        self.base = f"https://graph.facebook.com/{version}"
        timeout = httpx.Timeout(connect=10, read=45, write=30, pool=10)
        self.http = httpx.AsyncClient(timeout=timeout, limits=httpx.Limits(max_connections=100,
                                                                           max_keepalive_connections=20))

    @property
    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    @staticmethod
    def _error_text(r: httpx.Response) -> str:
        body = ""
        try:
            body = r.json()
        except ValueError:
            body = r.text
        if isinstance(body, dict):
            error = body.get("error", {})
            message = error.get("message") or body.get("message")
            code = error.get("code") or body.get("code")
            if message or code is not None:
                return f"WhatsApp API {r.status_code} ({code}): {message or body}"
        return f"WhatsApp API {r.status_code}: {r.text}"

    @staticmethod
    def _check(r: httpx.Response) -> httpx.Response:
        if r.is_error:
            raise RuntimeError(WhatsApp._error_text(r))
        return r

    async def validate_phone_access(self) -> None:
        r = await self.http.get(f"{self.base}/{self.phone_id}", headers=self._auth)
        if r.is_error:
            text = self._error_text(r)
            lower = text.lower()
            if "viewer cannot access the provided paid message account" in lower or "cannot access the provided paid message account" in lower:
                raise RuntimeError(
                    "El token de WhatsApp no tiene acceso al número configurado. "
                    f"Revisa WHATSAPP_TOKEN y PHONE_NUMBER_ID/PRIMARY_PHONE_NUMBER_ID. "
                    f"Debe usar el token de la misma cuenta de Meta / WhatsApp Business. Detalle: {text}"
                )
            self._check(r)

    async def send_text(self, to: str, text: str) -> None:
        # Limite de WhatsApp: 4096 caracteres por mensaje
        for i in range(0, len(text), 4000):
            r = await self.http.post(
                f"{self.base}/{self.phone_id}/messages",
                headers=self._auth,
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": text[i : i + 4000]},
                },
            )
            self._check(r)

    async def download_media(self, media_id: str) -> bytes:
        meta = self._check(await self.http.get(f"{self.base}/{media_id}", headers=self._auth))
        media_url = meta.json().get("url")
        if not isinstance(media_url, str):
            raise RuntimeError("WhatsApp devolvió una URL de medio inválida")
        parsed = urlparse(media_url)
        host = (parsed.hostname or "").lower()
        trusted_host = host == "graph.facebook.com" or host.endswith((".fbcdn.net", ".fbsbx.com"))
        if parsed.scheme != "https" or not trusted_host:
            raise RuntimeError("WhatsApp devolvió un host de medio no permitido")

        max_bytes = 16 * 1024 * 1024
        parts = []
        total = 0
        async with self.http.stream("GET", media_url, headers=self._auth) as response:
            self._check(response)
            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                raise RuntimeError("El archivo de WhatsApp supera el límite de 16 MB")
            async for part in response.aiter_bytes():
                total += len(part)
                if total > max_bytes:
                    raise RuntimeError("El archivo de WhatsApp supera el límite de 16 MB")
                parts.append(part)
        return b"".join(parts)

    async def upload_media(self, data: bytes, mime: str, filename: str) -> str:
        r = await self.http.post(
            f"{self.base}/{self.phone_id}/media",
            headers=self._auth,
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (filename, data, mime)},
        )
        return self._check(r).json()["id"]

    async def send_document(self, to: str, media_id: str, filename: str, caption: str) -> None:
        r = await self.http.post(
            f"{self.base}/{self.phone_id}/messages",
            headers=self._auth,
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "document",
                "document": {"id": media_id, "filename": filename, "caption": caption},
            },
        )
        self._check(r)
