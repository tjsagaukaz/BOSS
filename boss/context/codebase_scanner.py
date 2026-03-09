from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from boss.types import CodebaseScanResult, ScannedFile


class CodebaseScanner:
    IGNORED_DIRS = {
        ".git",
        ".venv",
        ".boss_benchmark_venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        ".idea",
        ".vscode",
    }
    BINARY_EXTENSIONS = {
        ".a",
        ".bin",
        ".class",
        ".dll",
        ".dylib",
        ".exe",
        ".gif",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".o",
        ".obj",
        ".pdf",
        ".png",
        ".so",
        ".svg",
        ".tar",
        ".wav",
        ".zip",
    }
    LANGUAGE_MAP = {
        ".py": "Python",
        ".ts": "TypeScript",
        ".tsx": "TypeScript",
        ".js": "JavaScript",
        ".jsx": "JavaScript",
        ".cxx": "C++",
        ".cc": "C++",
        ".cpp": "C++",
        ".hpp": "C++",
        ".hh": "C++",
        ".h": "C++",
        ".swift": "Swift",
        ".rs": "Rust",
        ".go": "Go",
        ".json": "JSON",
        ".md": "Markdown",
        ".toml": "TOML",
        ".yaml": "YAML",
        ".yml": "YAML",
        ".txt": "Text",
        ".sh": "Shell",
    }
    IMPORTANT_FILES = {
        "main.py",
        "manage.py",
        "app.py",
        "server.py",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "requirements.txt",
        "pyproject.toml",
        "cargo.toml",
        "go.mod",
        "package.swift",
        "readme.md",
        "main.ts",
        "main.js",
        "index.ts",
        "index.js",
        "router.py",
    }
    ENTRY_POINT_PATTERNS = (
        re.compile(r"(^|/)(main|app|server|index|cli)\.(py|ts|tsx|js|jsx|go|rs|swift|cpp)$", re.IGNORECASE),
        re.compile(r"(^|/)cmd/[^/]+/main\.go$", re.IGNORECASE),
    )

    def scan(self, project_name: str, project_root: str | Path) -> CodebaseScanResult:
        root = Path(project_root).resolve()
        files: list[ScannedFile] = []
        language_counter: Counter[str] = Counter()

        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(root).parts
            if any(part in self.IGNORED_DIRS for part in relative_parts[:-1]):
                continue
            if path.suffix.lower() in self.BINARY_EXTENSIONS or self._is_binary(path):
                continue

            language = self.LANGUAGE_MAP.get(path.suffix.lower(), "Other")
            relative_path = str(path.relative_to(root))
            stat = path.stat()
            scanned_file = ScannedFile(
                relative_path=relative_path,
                absolute_path=path,
                language=language,
                size=stat.st_size,
                modified_at=stat.st_mtime,
                content_hash=self._hash_file(path),
                is_important=self._is_important_file(path),
                is_entry_point=self._is_entry_point(relative_path),
            )
            files.append(scanned_file)
            language_counter[language] += 1

        files.sort(key=lambda item: item.relative_path)
        important_files = [item.relative_path for item in files if item.is_important][:25]
        if not important_files:
            important_files = [item.relative_path for item in files[:15]]

        entry_points = [item.relative_path for item in files if item.is_entry_point][:20]
        main_modules = self._main_modules(files)
        dependencies = self._detect_dependencies(root)

        return CodebaseScanResult(
            project_name=project_name,
            root=root,
            files=files,
            languages=dict(language_counter),
            important_files=important_files,
            entry_points=entry_points,
            main_modules=main_modules,
            dependencies=dependencies,
        )

    def _is_binary(self, path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                chunk = handle.read(4096)
        except OSError:
            return True
        if b"\x00" in chunk:
            return True
        if not chunk:
            return False
        text_chars = sum(byte in b"\t\n\r\f\b" or 32 <= byte <= 126 for byte in chunk)
        return text_chars / len(chunk) < 0.75

    def _hash_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _is_important_file(self, path: Path) -> bool:
        relative = str(path).lower()
        return path.name.lower() in self.IMPORTANT_FILES or "router" in relative or "service" in relative

    def _is_entry_point(self, relative_path: str) -> bool:
        return any(pattern.search(relative_path) for pattern in self.ENTRY_POINT_PATTERNS)

    def _main_modules(self, files: list[ScannedFile]) -> list[str]:
        module_counter: defaultdict[str, int] = defaultdict(int)
        for file in files:
            parts = Path(file.relative_path).parts
            if len(parts) < 2:
                continue
            top_level = parts[0]
            if top_level.startswith("."):
                continue
            if file.language in {"Python", "TypeScript", "JavaScript", "Go", "Rust", "Swift", "C++"}:
                module_counter[f"{top_level}/"] += 1
        return [name for name, _ in sorted(module_counter.items(), key=lambda item: (-item[1], item[0]))[:12]]

    def _detect_dependencies(self, root: Path) -> list[str]:
        dependencies: set[str] = set()

        package_json = root / "package.json"
        if package_json.exists():
            try:
                payload = json.loads(package_json.read_text(encoding="utf-8"))
                dependencies.update((payload.get("dependencies") or {}).keys())
                dependencies.update((payload.get("devDependencies") or {}).keys())
            except Exception:
                pass

        requirements = root / "requirements.txt"
        if requirements.exists():
            for line in requirements.read_text(encoding="utf-8", errors="replace").splitlines():
                normalized = line.strip()
                if not normalized or normalized.startswith("#"):
                    continue
                package = re.split(r"[<>=~! ]", normalized, maxsplit=1)[0]
                if package:
                    dependencies.add(package)

        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text(encoding="utf-8", errors="replace")
            dependencies.update(re.findall(r'"([^"]+)"', "\n".join(self._section_lines(content, "dependencies"))))

        cargo_toml = root / "Cargo.toml"
        if cargo_toml.exists():
            content = cargo_toml.read_text(encoding="utf-8", errors="replace")
            for line in self._section_lines(content, "dependencies"):
                match = re.match(r"\s*([A-Za-z0-9_-]+)\s*=", line)
                if match:
                    dependencies.add(match.group(1))

        go_mod = root / "go.mod"
        if go_mod.exists():
            for line in go_mod.read_text(encoding="utf-8", errors="replace").splitlines():
                match = re.match(r"\s*require\s+([^\s]+)", line)
                if match:
                    dependencies.add(match.group(1))

        package_swift = root / "Package.swift"
        if package_swift.exists():
            content = package_swift.read_text(encoding="utf-8", errors="replace")
            dependencies.update(re.findall(r'package\(url:\s*"([^"]+)"', content))

        return sorted(dependencies)

    def _section_lines(self, content: str, section_name: str) -> list[str]:
        active = False
        lines: list[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                active = section_name in stripped
                continue
            if active:
                lines.append(line)
        return lines
