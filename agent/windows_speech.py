"""Local Windows speech synthesis through a reusable System.Speech process."""

import asyncio
import base64
from contextlib import suppress
import os
from pathlib import Path
import struct
import subprocess

from livekit.agents import APIConnectionError, tts, utils
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS


_SAMPLE_RATE = 24000
_SYNTHESIZE = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$speech = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$audio = [IO.MemoryStream]::new()
$output = [IO.BinaryWriter]::new([Console]::OpenStandardOutput())
try {
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
        24000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono)
    $maleVoice = $speech.GetInstalledVoices() | Where-Object {
        $_.Enabled -and $_.VoiceInfo.Gender -eq 'Male'
    } | Select-Object -First 1
    if ($maleVoice) { $speech.SelectVoice($maleVoice.VoiceInfo.Name) }
    while ($null -ne ($line = [Console]::In.ReadLine())) {
        try {
            $text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($line))
            $audio.SetLength(0)
            $audio.Position = 0
            $speech.SetOutputToAudioStream($audio, $format)
            try { $speech.Speak($text) } finally { $speech.SetOutputToNull() }
            $bytes = $audio.ToArray()
            $output.Write([int]$bytes.Length)
            $output.Write([byte[]]$bytes)
        } catch {
            $output.Write([int]0)
        }
        $output.Flush()
    }
} finally {
    $speech.Dispose()
    $audio.Dispose()
    $output.Dispose()
}
"""


class WindowsSpeechTTS(tts.TTS):
    def __init__(self):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=_SAMPLE_RATE,
            num_channels=1,
        )
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def provider(self) -> str:
        return "Windows"

    @property
    def model(self) -> str:
        return "System.Speech"

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return _WindowsSpeechStream(tts=self, input_text=text, conn_options=conn_options)

    async def warm_up(self) -> None:
        await self._render("Ready.", timeout=10)

    async def aclose(self) -> None:
        self._closed = True
        async with self._lock:
            await self._stop_process()

    async def _stop_process(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            await process.communicate()

    async def _render(self, text: str, *, timeout: float) -> bytes:
        async with self._lock:
            if self._closed:
                raise APIConnectionError("Windows speech synthesis is closed", retryable=False)
            try:
                async with asyncio.timeout(timeout):
                    if self._process is None or self._process.returncode is not None:
                        powershell = (
                            Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
                        )
                        self._process = await asyncio.create_subprocess_exec(
                            str(powershell), "-NoProfile", "-NonInteractive", "-Command", _SYNTHESIZE,
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    stdin, stdout = self._process.stdin, self._process.stdout
                    assert stdin is not None and stdout is not None
                    stdin.write(base64.b64encode(text.encode("utf-8")) + b"\n")
                    await stdin.drain()
                    (length,) = struct.unpack("<I", await stdout.readexactly(4))
                    if not length or length % 2:
                        raise APIConnectionError("Windows speech returned invalid PCM audio", retryable=False)
                    return await stdout.readexactly(length)
            except (OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
                await self._stop_process()
                raise APIConnectionError("Could not synthesize speech locally", retryable=False) from exc
            except (APIConnectionError, asyncio.CancelledError):
                # A partial read leaves the next reply out of sync with the audio stream.
                await self._stop_process()
                raise


class _WindowsSpeechStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        assert isinstance(self._tts, WindowsSpeechTTS)
        audio = await self._tts._render(self.input_text, timeout=self._conn_options.timeout)
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=_SAMPLE_RATE,
            num_channels=1,
            mime_type="audio/pcm",
        )
        output_emitter.push(audio)
