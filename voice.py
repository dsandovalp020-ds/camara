"""Transcripcion local de notas de voz (faster-whisper). Claude no recibe audio, solo el texto."""
import asyncio
import io
import logging
import os
import threading

import av
import numpy as np

log = logging.getLogger("voice")
MODEL_SIZE = os.getenv("WHISPER_MODEL", "small")  # tiny | base | small | medium (mas grande = mejor y mas lento)
_model = None
_lock = threading.Lock()


def load():
    """Carga el modelo una sola vez (la primera vez lo descarga, ~500 MB para 'small')."""
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            log.info("Cargando modelo de voz '%s'...", MODEL_SIZE)
            _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
            log.info("Modelo de voz listo.")
    return _model


def _decode(data: bytes) -> np.ndarray:
    """OGG/Opus (u otro formato) -> mono 16 kHz float32, lo que espera Whisper."""
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    chunks = []
    with av.open(io.BytesIO(data)) as container:
        for frame in container.decode(audio=0):
            chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
    chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(None)]
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def _transcribe(data: bytes) -> str:
    segments, info = load().transcribe(_decode(data), vad_filter=True)
    text = " ".join(s.text.strip() for s in segments).strip()
    log.info("Audio transcrito (%s, %.0f s): %d caracteres", info.language, info.duration, len(text))
    return text


async def transcribe(data: bytes) -> str:
    """Devuelve el texto del audio ('' si no se reconoce voz). Corre en un hilo para no bloquear el servidor."""
    return await asyncio.to_thread(_transcribe, data)
