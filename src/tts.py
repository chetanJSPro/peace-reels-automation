from __future__ import annotations

from pathlib import Path


def synthesize_kokoro(
    text: str,
    out_path: str | Path,
    *,
    lang_code: str = "h",
    voice_id: str = "hm_omega",
    speed: float = 0.94,
) -> Path:
    """Generate local TTS with Kokoro.

    Hindi voices commonly available in Kokoro:
    - hf_alpha, hf_beta (female)
    - hm_omega, hm_psi (male)

    Kokoro outputs 24kHz WAV and is Apache-licensed upstream, but always check model/package
    license before commercial use.
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
    chunks = []
    generator = pipeline(text, voice=voice_id, speed=speed, split_pattern=r"\n+")
    for _graphemes, _phonemes, audio in generator:
        chunks.append(audio)
    if not chunks:
        raise RuntimeError("Kokoro returned no audio chunks")
    audio_all = np.concatenate(chunks)
    sf.write(str(out), audio_all, 24000)
    return out
