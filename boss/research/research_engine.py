from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

try:  # pragma: no cover - dependency availability is environment specific
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

from boss.types import ProjectContext, ResearchReport, ResearchSource


class ResearchEngine:
    SEARCH_GLOBS = [
        "!.git",
        "!node_modules",
        "!dist",
        "!build",
        "!.venv",
        "!Library",
        "!.Trash",
    ]

    def __init__(self, router, roots_registry, permission_manager, workspace_root: str | Path) -> None:
        self.router = router
        self.roots_registry = roots_registry
        self.permission_manager = permission_manager
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.logger = logging.getLogger(self.__class__.__name__)

    def research(
        self,
        query: str,
        *,
        project_name: str | None = None,
        project_context: ProjectContext | None = None,
        use_web: bool = True,
        use_local: bool = True,
    ) -> ResearchReport:
        started = time.perf_counter()
        local_results = self._local_results(query, project_context=project_context if use_local else None)
        local_sources = self._local_sources(local_results)

        web_text = ""
        web_sources: list[ResearchSource] = []
        metadata: dict[str, Any] = {}
        if use_web and self.permission_manager.web_research_allowed():
            try:
                web_text, web_sources = self._web_research(query)
            except Exception as exc:  # pragma: no cover - defensive around live provider issues
                metadata["web_error"] = str(exc)
                self.logger.warning("Web research failed: %s", exc)

        summary = self._synthesize_report(
            query=query,
            project_context=project_context,
            local_results=local_results,
            web_summary=web_text,
            sources=local_sources + web_sources,
        )
        findings = [self._compact(item.get("text", "")) for item in local_results[:3] if str(item.get("text", "")).strip()]
        recommendations = []
        if project_context is not None and project_context.project_brain is not None:
            recommendations = list(project_context.project_brain.next_priorities[:3])

        return ResearchReport(
            query=query,
            summary=summary,
            findings=findings,
            recommendations=recommendations,
            sources=[*local_sources, *web_sources],
            local_results=local_results,
            project_name=project_name,
            project_scope=project_name or "workspace",
            duration_seconds=time.perf_counter() - started,
            metadata=metadata,
        )

    def _local_results(self, query: str, *, project_context: ProjectContext | None = None) -> list[dict[str, Any]]:
        roots: list[Path] = []
        if project_context is not None:
            roots.append(project_context.root.resolve())
        for root in self.roots_registry.search_roots():
            resolved = Path(root).expanduser().resolve()
            if resolved not in roots:
                roots.append(resolved)
        if not roots:
            roots.append(self.workspace_root)

        if shutil.which("rg") is None:
            return []

        command = ["rg", "--vimgrep", "--no-messages", "-F", "-i", "-m", "1"]
        for glob in self.SEARCH_GLOBS:
            command.extend(["--glob", glob])
        command.append(query)

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for root in roots:
            response = subprocess.run(
                [*command, str(root)],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if response.returncode not in {0, 1} and not response.stdout.strip():
                continue
            for line in response.stdout.splitlines():
                parts = line.split(":", 3)
                if len(parts) != 4:
                    continue
                file_path, line_number, _column, text = parts
                absolute = Path(file_path).resolve()
                scoped = str(absolute)
                if scoped in seen:
                    continue
                seen.add(scoped)
                relative = self._relative_to_any_root(absolute, [root, self.workspace_root])
                results.append(
                    {
                        "file_path": relative,
                        "absolute_path": str(absolute),
                        "line_number": int(line_number),
                        "text": text.strip(),
                        "root": str(root),
                    }
                )
                if len(results) >= 8:
                    return results
        return results

    def _local_sources(self, local_results: list[dict[str, Any]]) -> list[ResearchSource]:
        sources: list[ResearchSource] = []
        for index, item in enumerate(local_results[:5], start=1):
            file_path = str(item.get("file_path", ""))
            line_number = int(item.get("line_number", 0) or 0)
            sources.append(
                ResearchSource(
                    source_type="local_file",
                    title=file_path or f"Local file {index}",
                    citation=f"[L{index}]",
                    file_path=file_path,
                    snippet=f"line {line_number}: {item.get('text', '')}".strip(),
                    metadata={"line_number": line_number, "root": item.get("root", "")},
                )
            )
        return sources

    def _web_research(self, query: str) -> tuple[str, list[ResearchSource]]:
        if OpenAI is None:
            raise RuntimeError("openai package is not installed.")
        cfg = self._research_model_config()
        if cfg is None or cfg.provider.lower() != "openai":
            raise RuntimeError("No OpenAI model is configured for research.")
        client = OpenAI()
        response = client.responses.create(
            model=cfg.model,
            instructions=(
                "You are BOSS Research Mode. Research the user's query on the web, then respond with a concise factual"
                " brief, a short list of findings, and any recommended next technical actions."
            ),
            input=[{"role": "user", "content": query}],
            tools=[{"type": "web_search_preview"}],
            timeout=120,
        )
        text = getattr(response, "output_text", "") or self._extract_text_from_response(response)
        sources = self._extract_web_sources(response)
        return text.strip(), sources

    def _research_model_config(self):
        config = self.router.config.models
        for role in ("research", "conversation", "engineer", "test"):
            cfg = config.get(role)
            if cfg is not None and cfg.provider.lower() == "openai":
                return cfg
        return None

    def _synthesize_report(
        self,
        *,
        query: str,
        project_context: ProjectContext | None,
        local_results: list[dict[str, Any]],
        web_summary: str,
        sources: list[ResearchSource],
    ) -> str:
        source_lines = "\n".join(
            f"{source.citation} {source.title} {source.url or source.file_path}\n{source.snippet}".strip()
            for source in sources[:8]
        )
        local_text = "\n".join(
            f"- {item['file_path']} line {item['line_number']}: {item['text']}" for item in local_results[:6]
        )
        project_summary = project_context.summary if project_context is not None else f"Workspace rooted at {self.workspace_root}"
        prompt = "\n".join(
            [
                f"Query: {query}",
                f"Scope: {project_context.name if project_context else 'workspace'}",
                f"Project Summary: {project_summary}",
                "Local Findings:",
                local_text or "- None",
                "Web Findings:",
                web_summary or "No web findings collected.",
                "Sources:",
                source_lines or "No sources collected.",
                "Write a concise research memo with sections: Summary, Findings, Recommended Next Steps, Sources.",
            ]
        )
        try:
            client = self.router.client_for_request("conversation", prompt=prompt, tools=[], request_options={"mode": "research"})
            result = client.generate(prompt=prompt, system_prompt="You are BOSS Research Mode.", tools=[])
            return result.text.strip() or self._fallback_summary(query, local_results, web_summary, sources)
        except Exception:  # pragma: no cover - runtime fallback
            return self._fallback_summary(query, local_results, web_summary, sources)

    def _fallback_summary(
        self,
        query: str,
        local_results: list[dict[str, Any]],
        web_summary: str,
        sources: list[ResearchSource],
    ) -> str:
        lines = [f"Summary: Research for '{query}'."]
        if web_summary:
            lines.append(f"Web Findings: {self._compact(web_summary, 500)}")
        if local_results:
            lines.append("Local Findings:")
            lines.extend(
                f"- {item['file_path']} line {item['line_number']}: {self._compact(str(item['text']), 160)}"
                for item in local_results[:4]
            )
        if sources:
            lines.append("Sources:")
            lines.extend(
                f"- {source.citation} {source.title} {source.url or source.file_path}".strip()
                for source in sources[:6]
            )
        return "\n".join(lines)

    def _extract_text_from_response(self, response: Any) -> str:
        output = getattr(response, "output", None) or []
        parts: list[str] = []
        for item in output:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", "")
                if text:
                    parts.append(text)
        return "\n".join(parts)

    def _extract_web_sources(self, response: Any) -> list[ResearchSource]:
        sources: list[ResearchSource] = []
        seen: set[str] = set()
        output = getattr(response, "output", None) or []
        counter = 1
        for item in output:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []) or []:
                annotations = getattr(content, "annotations", None) or []
                for annotation in annotations:
                    url = str(getattr(annotation, "url", "") or getattr(annotation, "uri", "") or "").strip()
                    title = str(getattr(annotation, "title", "") or url or f"Web source {counter}").strip()
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    sources.append(
                        ResearchSource(
                            source_type="web",
                            title=title,
                            citation=f"[W{counter}]",
                            url=url,
                            snippet=self._compact(str(getattr(content, "text", "") or ""), 240),
                        )
                    )
                    counter += 1
        if sources:
            return sources

        try:
            raw_output = json.dumps(getattr(response, "output", None), default=str)
        except TypeError:  # pragma: no cover
            raw_output = str(getattr(response, "output", ""))
        for token in raw_output.split('"'):
            if token.startswith("http://") or token.startswith("https://"):
                if token in seen:
                    continue
                seen.add(token)
                sources.append(
                    ResearchSource(
                        source_type="web",
                        title=token,
                        citation=f"[W{counter}]",
                        url=token,
                    )
                )
                counter += 1
                if len(sources) >= 6:
                    break
        return sources

    def _relative_to_any_root(self, path: Path, roots: list[Path]) -> str:
        for root in roots:
            try:
                return str(path.relative_to(root))
            except ValueError:
                continue
        return str(path)

    def _compact(self, text: str, limit: int = 200) -> str:
        value = " ".join(text.split())
        if len(value) <= limit:
            return value
        return value[: limit - 1].rstrip() + "…"
