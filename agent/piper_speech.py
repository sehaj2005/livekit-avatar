"""Local text-to-speech for the Linux meeting worker."""

import asyncio
from pathlib import Path
from threading import Lock

from livekit.agents import APIConnectionError, tts, utils
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from piper import PiperVoice


class PiperTTS(tts.TTS):
    def __init__(self, model_path: Path):
        self._voice = PiperVoice.load(model_path)
        self._voice_name = model_path.stem
        self._lock = Lock()
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=self._voice.config.sample_rate,
            num_channels=1,
        )

    @property
    def provider(self) -> str:
        return "Piper"

    @property
    def model(self) -> str:
        return self._voice_name

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return _PiperSpeechStream(tts=self, input_text=text, conn_options=conn_options)

    def _render(self, text: str) -> bytes:
        with self._lock:
            return b"".join(chunk.audio_int16_bytes for chunk in self._voice.synthesize(text))


class _PiperSpeechStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        assert isinstance(self._tts, PiperTTS)
        try:
            audio = await asyncio.wait_for(
                asyncio.to_thread(self._tts._render, self.input_text),
                timeout=self._conn_options.timeout,
            )
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            raise APIConnectionError("Could not synthesize speech with Piper", retryable=False) from exc
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
            mime_type="audio/pcm",
        )
        output_emitter.push(audio)
