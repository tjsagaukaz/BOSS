from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Iterable

try:  # pragma: no cover - dependency import is environment specific
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None


class EmbeddingService:
    def __init__(self, provider: str = "local", model: str = "hashed-256", dimensions: int = 256) -> None:
        self.provider = provider
        self.model = model
        self.dimensions = dimensions
        self._openai_client = None

    def embed(self, text: str, force_local: bool = False) -> list[float]:
        if force_local:
            return self._embed_locally(text)
        if self.provider == "openai":
            try:
                return self._embed_with_openai(text)
            except Exception:
                return self._embed_locally(text)
        return self._embed_locally(text)

    def cosine_similarity(self, left: Iterable[float], right: Iterable[float]) -> float:
        left_list = list(left)
        right_list = list(right)
        if not left_list or not right_list or len(left_list) != len(right_list):
            return 0.0
        return sum(a * b for a, b in zip(left_list, right_list))

    def _embed_locally(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = re.findall(r"[a-zA-Z0-9_./-]+", text.lower())
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            weight = 1.0 + min(len(token), 16) / 16.0
            vector[index] += sign * weight
        norm = math.sqrt(sum(component * component for component in vector)) or 1.0
        return [component / norm for component in vector]

    def _embed_with_openai(self, text: str) -> list[float]:
        if OpenAI is None:
            raise RuntimeError("The openai package is not installed.")
        if self._openai_client is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not set.")
            self._openai_client = OpenAI(api_key=api_key)
        response = self._openai_client.embeddings.create(model=self.model, input=text)
        return list(response.data[0].embedding)
