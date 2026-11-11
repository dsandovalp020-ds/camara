"""Base de conocimiento: archivos .md/.txt en la carpeta knowledge/. Los PDF se transcriben una vez."""
import asyncio
import base64
import io
import os
import pathlib

from pypdf import PdfReader, PdfWriter

KB_DIR = pathlib.Path(os.getenv("KB_DIR", "knowledge"))
PAGES_PER_CHUNK = int(os.getenv("PAGES_PER_CHUNK", "4"))
MODEL_EXTRACT = os.getenv("MODEL_EXTRACT", "claude-sonnet-5-5")

TRANSCRIBE = """Transcribe fielmente este PDF a Markdown, en su idioma original. Conserva TODAS las cifras, \
fechas, nombres, direcciones, telefonos, correos, tablas y condiciones tal como aparecen. No resumas, no \
interpretes y no agregues nada. Si una parte es ilegible, escribe [ilegible]. Devuelve solo el Markdown."""


def load_knowledge() -> str:
    parts = []
    for p in sorted(KB_DIR.glob("*")):
        if p.suffix.lower() in {".md", ".txt"} and not p.name.startswith("_"):
            parts.append(f"=== DOCUMENTO: {p.name} ===\n{p.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts)


def approx_tokens(text: str) -> int:
    return int(len(text) / 3.5)


def _split(pdf_bytes: bytes) -> list[bytes]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if reader.is_encrypted:
        raise ValueError("El PDF esta protegido con contrasena.")
    chunks = []
    for start in range(0, len(reader.pages), PAGES_PER_CHUNK):
        w = PdfWriter()
        for i in range(start, min(start + PAGES_PER_CHUNK, len(reader.pages))):
            w.add_page(reader.pages[i])
        b = io.BytesIO()
        w.write(b)
        chunks.append(b.getvalue())
    return chunks


async def pdf_to_markdown(pdf_bytes: bytes) -> str:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic()
    sem = asyncio.Semaphore(3)

    async def one(chunk: bytes) -> str:
        async with sem:
            r = await client.messages.create(
                model=MODEL_EXTRACT, max_tokens=16000, system=TRANSCRIBE,
                messages=[{"role": "user", "content": [
                    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                    "data": base64.standard_b64encode(chunk).decode()}},
                    {"type": "text", "text": "Transcribe este documento."}]}])
            if r.stop_reason == "max_tokens":
                raise ValueError("Un fragmento es demasiado grande; baja PAGES_PER_CHUNK en el .env.")
            return "".join(b.text for b in r.content if b.type == "text").strip()

    return "\n\n".join(await asyncio.gather(*(one(c) for c in _split(pdf_bytes))))
