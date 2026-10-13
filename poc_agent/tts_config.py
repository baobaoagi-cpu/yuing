"""MiniMax output contract for LiveKit's streaming audio emitter."""

from __future__ import annotations

import os


def minimax_tts_options(api_key: str) -> dict[str, str | int]:
    return {
        "model": os.environ.get("MINIMAX_TTS_MODEL", "speech-2.8-turbo"),
        "voice": os.environ.get("MINIMAX_VOICE_ID", "socialmedia_female_2_v1"),
        "api_key": api_key,
        "base_url": os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.cn"),
        # LiveKit MiniMax 1.8.6 defaults to MP3. Multiple MP3 segments from one
        # streamed reply can invalidate the decoder; raw PCM avoids that path.
        "audio_format": "pcm",
        "sample_rate": 24000,
    }
