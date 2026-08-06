from __future__ import annotations

import random
from pathlib import Path


def synthesize_kokoro(
    text: str,
    out_path: str | Path,
    *,
    lang_code: str = "h",
    voice_id: str = "hm_omega",
    speed: float = 0.94,
    pause_seconds: float = 0.55,
) -> Path:
    """Generate local TTS with Kokoro.

    Hindi voices commonly available in Kokoro:
    - hf_alpha, hf_beta (female)
    - hm_omega, hm_psi (male)

    Kokoro outputs 24kHz WAV and is Apache-licensed upstream, but always check model/package
    license before commercial use.

    `pause_seconds` is the *average* silence inserted between lines (Kokoro itself only has a
    brief natural gap per chunk). Real speech doesn't pause the same length after every sentence
    — a fixed gap is what makes TTS sound metronomic/robotic — so each gap is jittered around
    that average, and every chunk gets a short fade in/out to avoid audible clicks at the splice
    points. This is about pacing/delivery only; it doesn't alter or clone anyone's actual voice.
    """
    try:
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline
    except Exception as e:
        raise RuntimeError(
            "Kokoro TTS is not installed/working. Install requirements and OS espeak-ng. "
            "Linux: sudo apt install espeak-ng && pip install -r requirements.txt"
        ) from e

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pipeline = KPipeline(lang_code=lang_code)
    sample_rate = 24000
    fade_len = int(0.02 * sample_rate)  # 20ms fade in/out, kills splice clicks

    def _faded(audio) -> "np.ndarray":
        audio = np.asarray(audio, dtype=np.float32).copy()  # Kokoro yields torch Tensors
        n = min(fade_len, len(audio) // 2)
        if n > 0:
            ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
            audio[:n] *= ramp
            audio[-n:] *= ramp[::-1]
        return audio

    chunks = []
    generator = pipeline(text, voice=voice_id, speed=speed, split_pattern=r"\n+")
    for _graphemes, _phonemes, audio in generator:
        if chunks:
            gap = random.uniform(pause_seconds * 0.6, pause_seconds * 1.5)
            chunks.append(np.zeros(int(gap * sample_rate), dtype=np.float32))
        chunks.append(_faded(audio))
    if not chunks:
        raise RuntimeError("Kokoro returned no audio chunks")
    audio_all = np.concatenate(chunks)
    sf.write(str(out), audio_all, sample_rate)
    return out
