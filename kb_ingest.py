"""Agrega documentos a la base de conocimiento.

Uso:  python kb_ingest.py servicios.pdf estatuto.pdf eventos.md
Los PDF se transcriben a knowledge/<nombre>.md (revisa el resultado y corrige lo que haga falta).
Los .md/.txt se copian tal cual. Despues reinicia el agente o escribe /recargar desde WhatsApp.
"""
import asyncio
import pathlib
import shutil
import sys

from dotenv import load_dotenv

load_dotenv()

import kb  # noqa: E402


async def main(paths: list[str]) -> None:
    kb.KB_DIR.mkdir(exist_ok=True)
    for p in map(pathlib.Path, paths):
        out = kb.KB_DIR / (p.stem + ".md")
        if p.suffix.lower() == ".pdf":
            out.write_text(await kb.pdf_to_markdown(p.read_bytes()), encoding="utf-8")
        elif p.suffix.lower() in {".md", ".txt"}:
            shutil.copy(p, kb.KB_DIR / p.name)
            out = kb.KB_DIR / p.name
        else:
            print(f"Omitido (formato no soportado): {p.name}")
            continue
        print(f"OK  {out}")
    text = kb.load_knowledge()
    print(f"Base de conocimiento: ~{kb.approx_tokens(text):,} tokens")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1:]))
