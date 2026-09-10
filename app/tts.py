"""Local speech synthesis.

Exists for one reason: acoustic echo cancellation needs a reference signal.

The browser can only cancel the assistant's voice out of the microphone if the
browser is the thing playing it. `speechSynthesis` fails that test — on most
platforms it hands the text to the OS speech engine, which plays outside the
browser's audio render path, so Chrome's AEC has nothing to subtract and the
microphone hears every word. That is what makes barge-in impossible on a
speaker: no threshold separates "my own voice got louder" from "someone is
talking", and a sweep of every operating point either cut the answer off on its
own voice or missed real interruptions.

Synthesising here and returning audio the page plays through an <audio> element
puts the voice inside the browser's render mix, which is exactly the position
Meet's remote audio occupies. AEC then does the work it is built for.

Piper runs on CPU, offline: no API key, no per-word cost, no network hop on the
hot path. Measured on this machine at ~0.05x real time — a five-second answer
synthesises in about a quarter of a second.
"""
import asyncio
import hashlib
import io
import logging
import os
import wave
from collections import OrderedDict
from pathlib import Path
from typing import Optional

log = logging.getLogger("sarjy.tts")

DEFAULT_VOICE = str(
    Path.home() / ".local/share/piper-voices/en_US-amy-low.onnx"
)
# expanduser, because the natural thing to write in .env is a ~ path.
VOICE_PATH = str(Path(os.getenv("PIPER_VOICE", DEFAULT_VOICE)).expanduser())

#: Answers repeat -- the cache demo exists to make them repeat -- and
#: re-synthesising an identical sentence is pure waste. Small enough that the
#: memory cost is irrelevant; keyed by text so a cache hit upstream also skips
#: synthesis here.
_MAX_CACHED = 64
_audio: "OrderedDict[str, bytes]" = OrderedDict()

_voice = None
_load_failed = False
_lock = asyncio.Lock()


def available() -> bool:
    """False when synthesis cannot work, so the caller can fall back rather
    than fail. Reported by /health."""
    return not _load_failed and Path(VOICE_PATH).exists()


def status() -> str:
    if _load_failed:
        return "unavailable (load failed)"
    if not Path(VOICE_PATH).exists():
        return f"unavailable (no voice at {VOICE_PATH})"
    return "ok (loaded)" if _voice is not None else "ok (not yet loaded)"


def _load():
    """Import and model load are both deferred: the service must start and
    serve text without piper installed."""
    global _voice, _load_failed
    if _voice is not None or _load_failed:
        return _voice
    try:
        from piper import PiperVoice
        _voice = PiperVoice.load(VOICE_PATH)
        log.info("piper voice loaded from %s", VOICE_PATH)
    except Exception as exc:
        _load_failed = True
        log.warning("piper unavailable (%s) -- falling back to browser speech", exc)
    return _voice


def _synthesize_blocking(text: str) -> Optional[bytes]:
    voice = _load()
    if voice is None:
        return None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        voice.synthesize_wav(text, w)
    return buf.getvalue()


async def synthesize(text: str) -> Optional[bytes]:
    """WAV bytes for `text`, or None if synthesis is not available.

    Runs off the event loop: piper is CPU-bound, and blocking here would stall
    every other request for the duration.
    """
    text = (text or "").strip()
    if not text:
        return None

    key = hashlib.sha256(text.encode()).hexdigest()
    cached = _audio.get(key)
    if cached is not None:
        _audio.move_to_end(key)
        return cached

    # One synthesis at a time. The model is not documented as thread-safe, and
    # two concurrent calls on a CPU model race for the same cores anyway.
    async with _lock:
        cached = _audio.get(key)
        if cached is not None:
            return cached
        try:
            data = await asyncio.to_thread(_synthesize_blocking, text)
        except Exception as exc:
            log.warning("synthesis failed: %s", exc)
            return None
        if data:
            _audio[key] = data
            while len(_audio) > _MAX_CACHED:
                _audio.popitem(last=False)
        return data


async def warm() -> None:
    """Load the model at startup so the first answer is not the one that pays
    the ~0.9s load."""
    if not available():
        return
    await asyncio.to_thread(_load)
