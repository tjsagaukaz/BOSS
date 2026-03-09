from __future__ import annotations

import re
from typing import Any

try:  # pragma: no cover - optional runtime dependency
    import speech_recognition as sr
except ImportError:  # pragma: no cover
    sr = None


class VoiceInterface:
    def __init__(self) -> None:
        self._recognizer = sr.Recognizer() if sr is not None else None

    def available(self) -> bool:
        return self._recognizer is not None

    def capture_transcript(self, timeout: int = 5, phrase_time_limit: int = 15) -> str:
        if self._recognizer is None or sr is None:
            raise RuntimeError("SpeechRecognition is not installed. Install dependencies to enable voice commands.")
        with sr.Microphone() as source:
            self._recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio = self._recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        return self._recognizer.recognize_google(audio)

    def command_from_transcript(self, transcript: str) -> str:
        cleaned = transcript.strip()
        if not cleaned:
            raise ValueError("Transcript is empty.")
        command = re.sub(r"^\s*boss[\s,:-]*", "", cleaned, flags=re.IGNORECASE).strip()

        patterns = [
            (r"^(?:build|create|make)\s+(.+)$", lambda m: f'build "{m.group(1).strip()}"'),
            (r"^(?:plan)\s+(.+)$", lambda m: f'plan "{m.group(1).strip()}"'),
            (r"^(?:code|implement)\s+(.+)$", lambda m: f'code "{m.group(1).strip()}"'),
            (r"^(?:open project|project)\s+([a-zA-Z0-9._-]+)$", lambda m: f"project {m.group(1)}"),
            (r"^(?:open file|open)\s+(.+)$", lambda m: f'open {m.group(1).strip()}'),
            (r"^(?:jump|go to symbol)\s+([A-Za-z0-9_]+)$", lambda m: f"jump {m.group(1)}"),
            (r"^(?:search|find)\s+(.+)$", lambda m: f'search "{m.group(1).strip()}"'),
            (r"^(?:run tests|test)$", lambda _m: "test"),
            (r"^(status|dashboard|memory|solutions|graph|models|improve|evolve|tasks|agents|map|audit|learn|web)$", lambda m: m.group(1)),
        ]
        for pattern, builder in patterns:
            match = re.match(pattern, command, re.IGNORECASE)
            if match:
                return builder(match)
        return command

    def listen(self, transcript: str | None = None, timeout: int = 5, phrase_time_limit: int = 15) -> dict[str, Any]:
        heard = transcript if transcript is not None else self.capture_transcript(timeout=timeout, phrase_time_limit=phrase_time_limit)
        return {
            "transcript": heard,
            "command": self.command_from_transcript(heard),
        }
