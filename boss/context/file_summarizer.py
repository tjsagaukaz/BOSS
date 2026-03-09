from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from boss.types import FileSummary, ScannedFile


class FileSummarizer:
    MAX_PROMPT_CHARS = 12_000
    MAX_SNIPPET_CHARS = 2_000

    SYSTEM_PROMPT = """
You summarize a single source file for an AI coding agent.

Rules:
- summarize only this file, not the entire repository
- infer the file's purpose from the snippet, symbols, and dependencies provided
- keep the summary concise and concrete
- return strict JSON with keys: purpose, summary, symbols, dependencies
""".strip()

    def __init__(self, llm_client: Any | None = None) -> None:
        self.llm_client = llm_client
        self.logger = logging.getLogger(self.__class__.__name__)

    def summarize_file(self, scanned_file: ScannedFile, force_heuristic: bool = False) -> FileSummary:
        content = scanned_file.absolute_path.read_text(encoding="utf-8", errors="replace")
        symbols = self._extract_symbols(content, scanned_file.language)
        dependencies = self._extract_dependencies(content, scanned_file.language, scanned_file.absolute_path.name)
        snippets = self._extract_snippets(content, symbols)

        llm_result = self._summarize_with_llm(scanned_file, symbols, dependencies, snippets, force_heuristic=force_heuristic)
        if llm_result is None:
            llm_result = self._heuristic_summary(scanned_file, symbols, dependencies, snippets)

        purpose = llm_result.get("purpose") or self._fallback_purpose(scanned_file, symbols)
        summary = llm_result.get("summary") or self._fallback_summary(scanned_file, symbols, dependencies)
        summarized_symbols = self._clean_list(llm_result.get("symbols"), fallback=symbols)
        summarized_dependencies = self._clean_list(llm_result.get("dependencies"), fallback=dependencies)

        return FileSummary(
            file_path=scanned_file.relative_path,
            language=scanned_file.language,
            purpose=purpose.strip(),
            summary=summary.strip(),
            symbols=summarized_symbols[:15],
            dependencies=summarized_dependencies[:20],
            snippets=snippets[:3],
        )

    def _summarize_with_llm(
        self,
        scanned_file: ScannedFile,
        symbols: list[str],
        dependencies: list[str],
        snippets: list[str],
        force_heuristic: bool = False,
    ) -> dict[str, Any] | None:
        if force_heuristic or self.llm_client is None:
            return None

        prompt = self._build_prompt(scanned_file, symbols, dependencies, snippets)
        try:
            result = self.llm_client.generate(
                prompt=prompt,
                system_prompt=self.SYSTEM_PROMPT,
                max_tokens=700,
                temperature=0.1,
            )
        except Exception as exc:
            self.logger.warning("Falling back to heuristic summary for %s: %s", scanned_file.relative_path, exc)
            return None

        return self._parse_json_response(result.text)

    def _build_prompt(
        self,
        scanned_file: ScannedFile,
        symbols: list[str],
        dependencies: list[str],
        snippets: list[str],
    ) -> str:
        prompt = {
            "file_path": scanned_file.relative_path,
            "language": scanned_file.language,
            "size_bytes": scanned_file.size,
            "symbols": symbols[:20],
            "dependencies": dependencies[:20],
            "snippets": snippets[:3],
        }
        return json.dumps(prompt, indent=2)[: self.MAX_PROMPT_CHARS]

    def _parse_json_response(self, text: str) -> dict[str, Any] | None:
        payload = text.strip()
        if not payload:
            return None
        if payload.startswith("```"):
            payload = re.sub(r"^```[a-zA-Z0-9]*\n?", "", payload)
            payload = re.sub(r"\n?```$", "", payload)
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _heuristic_summary(
        self,
        scanned_file: ScannedFile,
        symbols: list[str],
        dependencies: list[str],
        snippets: list[str],
    ) -> dict[str, Any]:
        purpose = self._fallback_purpose(scanned_file, symbols)
        summary = self._fallback_summary(scanned_file, symbols, dependencies)
        return {
            "purpose": purpose,
            "summary": summary,
            "symbols": symbols[:10],
            "dependencies": dependencies[:12],
            "snippets": snippets[:3],
        }

    def _fallback_purpose(self, scanned_file: ScannedFile, symbols: list[str]) -> str:
        if symbols:
            return f"{scanned_file.relative_path} defines {', '.join(symbols[:3])} and related logic."
        return f"{scanned_file.relative_path} provides {scanned_file.language.lower()} implementation details."

    def _fallback_summary(
        self,
        scanned_file: ScannedFile,
        symbols: list[str],
        dependencies: list[str],
    ) -> str:
        parts = [
            f"Language: {scanned_file.language}.",
            f"Key symbols: {', '.join(symbols[:6]) or 'none detected'}.",
            f"Dependencies: {', '.join(dependencies[:8]) or 'none detected'}.",
        ]
        return " ".join(parts)

    def _extract_symbols(self, content: str, language: str) -> list[str]:
        patterns = {
            "Python": [r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)"],
            "TypeScript": [
                r"^\s*export\s+class\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"^\s*export\s+function\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)",
            ],
            "JavaScript": [
                r"^\s*export\s+class\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"^\s*export\s+function\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)",
            ],
            "Go": [r"^\s*func\s+([A-Za-z_][A-Za-z0-9_]*)", r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)"],
            "Rust": [r"^\s*fn\s+([A-Za-z_][A-Za-z0-9_]*)", r"^\s*(?:struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)"],
            "Swift": [r"^\s*func\s+([A-Za-z_][A-Za-z0-9_]*)", r"^\s*(?:class|struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)"],
            "C++": [r"^\s*(?:class|struct|namespace)\s+([A-Za-z_][A-Za-z0-9_]*)"],
        }
        candidates = patterns.get(language, [r"^\s*(?:class|def|function|func)\s+([A-Za-z_][A-Za-z0-9_]*)"])
        results: list[str] = []
        for line in content.splitlines():
            for pattern in candidates:
                match = re.search(pattern, line)
                if match:
                    results.append(match.group(1))
        return list(dict.fromkeys(results))[:20]

    def _extract_dependencies(self, content: str, language: str, file_name: str) -> list[str]:
        dependencies: list[str] = []

        if file_name == "package.json":
            try:
                payload = json.loads(content)
                dependencies.extend((payload.get("dependencies") or {}).keys())
                dependencies.extend((payload.get("devDependencies") or {}).keys())
            except Exception:
                pass
            return list(dict.fromkeys(dependencies))

        if file_name == "requirements.txt":
            for line in content.splitlines():
                normalized = line.strip()
                if not normalized or normalized.startswith("#"):
                    continue
                dependencies.append(re.split(r"[<>=~! ]", normalized, maxsplit=1)[0])
            return list(dict.fromkeys(dependencies))

        patterns = {
            "Python": [r"^\s*import\s+([A-Za-z0-9_\.]+)", r"^\s*from\s+([A-Za-z0-9_\.]+)\s+import"],
            "TypeScript": [r'from\s+["\']([^"\']+)["\']', r'require\(["\']([^"\']+)["\']\)'],
            "JavaScript": [r'from\s+["\']([^"\']+)["\']', r'require\(["\']([^"\']+)["\']\)'],
            "Go": [r'^\s*import\s+"([^"]+)"'],
            "Rust": [r"^\s*use\s+([A-Za-z0-9_:]+)"],
            "Swift": [r"^\s*import\s+([A-Za-z0-9_]+)"],
            "C++": [r'^\s*#include\s+[<"]([^>"]+)[>"]'],
        }
        for pattern in patterns.get(language, []):
            dependencies.extend(re.findall(pattern, content, flags=re.MULTILINE))

        cleaned = []
        for dependency in dependencies:
            value = dependency.strip()
            if value and not value.startswith("."):
                cleaned.append(value)
        return list(dict.fromkeys(cleaned))[:25]

    def _extract_snippets(self, content: str, symbols: list[str]) -> list[str]:
        lines = content.splitlines()
        if not lines:
            return []

        snippets: list[str] = []
        if symbols:
            for symbol in symbols[:3]:
                symbol_pattern = re.compile(rf"\b{re.escape(symbol)}\b")
                for index, line in enumerate(lines):
                    if symbol_pattern.search(line):
                        start = max(index - 3, 0)
                        end = min(index + 12, len(lines))
                        snippet = "\n".join(lines[start:end]).strip()
                        if snippet:
                            snippets.append(snippet[: self.MAX_SNIPPET_CHARS])
                        break

        if not snippets:
            snippet = "\n".join(lines[: min(40, len(lines))]).strip()
            if snippet:
                snippets.append(snippet[: self.MAX_SNIPPET_CHARS])

        return list(dict.fromkeys(snippets))

    def _clean_list(self, value: Any, fallback: list[str]) -> list[str]:
        if not isinstance(value, list):
            return fallback
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return cleaned or fallback
