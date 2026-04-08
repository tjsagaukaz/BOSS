from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agents import Runner
from pydantic import BaseModel, Field

from boss.agents import build_review_agent
from boss.config import settings
from boss.control import applicable_rules, is_path_allowed_for_agent, load_boss_control
from boss.memory.knowledge import Project, ProjectNote, get_knowledge_store
from boss.models import build_run_execution_options


_MAX_DIFF_CHARS = 40_000
_MAX_FILE_CHARS = 4_000
_MAX_DOC_CHARS = 6_000
_MAX_CONTEXT_FILES = 6
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewFinding(BaseModel):
    severity: Literal["critical", "high", "medium", "low"]
    file_path: str
    evidence: str
    risk: str
    recommended_fix: str


class ReviewReport(BaseModel):
    summary: str = Field(default="")
    residual_risk: str = Field(default="")
    findings: list[ReviewFinding] = Field(default_factory=list)


class ReviewRunRecord(BaseModel):
    review_id: str
    created_at: str
    title: str
    target_kind: str
    target_label: str
    scope_summary: str
    project_path: str | None = None
    repo_root: str | None = None
    base_ref: str | None = None
    head_ref: str | None = None
    file_paths: list[str] = Field(default_factory=list)
    summary: str = ""
    residual_risk: str = ""
    findings: list[ReviewFinding] = Field(default_factory=list)
    severity_counts: dict[str, int] = Field(default_factory=dict)


@dataclass(frozen=True)
class ReviewRequest:
    target: str = "auto"
    project_path: str | None = None
    base_ref: str | None = None
    head_ref: str | None = None
    file_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewMaterial:
    workspace_root: Path
    target_kind: str
    target_label: str
    scope_summary: str
    project_path: str | None
    repo_root: Path | None
    current_branch: str | None
    base_ref: str | None
    head_ref: str | None
    changed_files: tuple[str, ...]
    diff_stat: str
    diff_text: str
    file_contexts: tuple[tuple[str, str], ...]
    project_summaries: tuple[str, ...]
    docs_context: str


def review_capabilities(project_path: str | None = None) -> dict[str, Any]:
    workspace_root = _resolve_workspace_root(project_path)
    repo_root = _git_root(workspace_root)
    current_branch = _git_current_branch(repo_root) if repo_root else None
    working_tree_files = _git_name_only(repo_root, ["diff", "--name-only", "HEAD", "--"]) if repo_root else []
    staged_files = _git_name_only(repo_root, ["diff", "--cached", "--name-only", "--"]) if repo_root else []
    store = get_knowledge_store()
    indexed_project = _resolve_indexed_project(store, workspace_root, repo_root)
    indexed_summary_count = len(_project_summary_notes(store, indexed_project.path if indexed_project else None))

    available_targets = ["files"]
    if repo_root:
        available_targets.extend(["working_tree", "staged", "branch_diff"])
    if indexed_project and indexed_summary_count:
        available_targets.append("project_summary")

    default_target = "files"
    if working_tree_files:
        default_target = "working_tree"
    elif staged_files:
        default_target = "staged"
    elif indexed_project and indexed_summary_count:
        default_target = "project_summary"

    return {
        "workspace_root": str(workspace_root),
        "project_path": indexed_project.path if indexed_project else str(workspace_root),
        "repo_root": str(repo_root) if repo_root else None,
        "git_available": repo_root is not None,
        "current_branch": current_branch,
        "working_tree_files": working_tree_files,
        "staged_files": staged_files,
        "has_working_tree_changes": bool(working_tree_files),
        "has_staged_changes": bool(staged_files),
        "indexed_project_available": indexed_project is not None,
        "available_targets": available_targets,
        "default_target": default_target,
    }


def collect_review_material(request: ReviewRequest) -> ReviewMaterial:
    workspace_root = _resolve_workspace_root(request.project_path)
    capabilities = review_capabilities(str(workspace_root))
    requested_target = request.target.strip().lower() or "auto"
    target_kind = _resolve_target_kind(requested_target, capabilities, request.file_paths)
    repo_root = Path(capabilities["repo_root"]) if capabilities.get("repo_root") else None
    current_branch = capabilities.get("current_branch")
    store = get_knowledge_store()
    indexed_project = _resolve_indexed_project(store, workspace_root, repo_root)

    changed_files: list[str] = []
    diff_stat = ""
    diff_text = ""
    file_contexts: list[tuple[str, str]] = []
    project_summaries: list[str] = []
    base_ref = request.base_ref
    head_ref = request.head_ref

    if target_kind == "working_tree":
        if repo_root is None:
            raise ValueError("Git working tree review is unavailable because this project is not in a git repository.")
        diff_stat = _git_output(repo_root, ["diff", "--stat", "HEAD", "--"]) or ""
        diff_text = _git_output(repo_root, ["diff", "--find-renames", "--unified=3", "HEAD", "--"]) or ""
        changed_files = _git_name_only(repo_root, ["diff", "--name-only", "HEAD", "--"])
    elif target_kind == "staged":
        if repo_root is None:
            raise ValueError("Staged review is unavailable because this project is not in a git repository.")
        diff_stat = _git_output(repo_root, ["diff", "--cached", "--stat", "--"]) or ""
        diff_text = _git_output(repo_root, ["diff", "--cached", "--find-renames", "--unified=3", "--"]) or ""
        changed_files = _git_name_only(repo_root, ["diff", "--cached", "--name-only", "--"])
    elif target_kind == "branch_diff":
        if repo_root is None:
            raise ValueError("Branch review is unavailable because this project is not in a git repository.")
        if not base_ref or not head_ref:
            raise ValueError("Branch review requires both base_ref and head_ref.")
        diff_spec = f"{base_ref}...{head_ref}"
        diff_stat = _git_output(repo_root, ["diff", "--stat", diff_spec, "--"]) or ""
        diff_text = _git_output(repo_root, ["diff", "--find-renames", "--unified=3", diff_spec, "--"]) or ""
        changed_files = _git_name_only(repo_root, ["diff", "--name-only", diff_spec, "--"])
    elif target_kind == "files":
        file_contexts = _collect_file_contexts(workspace_root, request.file_paths)
        changed_files = [path for path, _content in file_contexts]
        if requested_target == "files" and not file_contexts:
            raise ValueError("Provide one or more readable local files for file review.")
    elif target_kind == "project_summary":
        project_summaries = _project_summary_sections(store, indexed_project)
    else:
        raise ValueError(f"Unsupported review target: {target_kind}")

    if target_kind in {"working_tree", "staged", "branch_diff"}:
        if not diff_text.strip() and not changed_files:
            raise ValueError("There is no diff content to review for the selected git target.")
        file_contexts = _collect_repo_file_contexts(repo_root, changed_files)
        project_summaries = _project_summary_sections(store, indexed_project)

    if target_kind == "files" and not file_contexts and indexed_project:
        project_summaries = _project_summary_sections(store, indexed_project)

    if target_kind == "project_summary" and not project_summaries:
        raise ValueError("No indexed project summaries are available for review.")

    if not diff_text.strip() and not file_contexts and not project_summaries:
        raise ValueError("No local evidence was available for review.")

    return ReviewMaterial(
        workspace_root=workspace_root,
        target_kind=target_kind,
        target_label=_target_label(target_kind, workspace_root, repo_root, base_ref, head_ref, changed_files),
        scope_summary=_scope_summary(target_kind, repo_root, current_branch, base_ref, head_ref, changed_files),
        project_path=indexed_project.path if indexed_project else str(workspace_root),
        repo_root=repo_root,
        current_branch=current_branch,
        base_ref=base_ref,
        head_ref=head_ref,
        changed_files=tuple(changed_files),
        diff_stat=_clip_text(diff_stat, _MAX_DIFF_CHARS // 3),
        diff_text=_clip_text(diff_text, _MAX_DIFF_CHARS),
        file_contexts=tuple(file_contexts),
        project_summaries=tuple(project_summaries),
        docs_context=_docs_context(workspace_root),
    )


async def run_review(request: ReviewRequest) -> ReviewRunRecord:
    material = collect_review_material(request)
    agent = build_review_agent(output_type=ReviewReport, workspace_root=material.workspace_root)
    execution_options = build_run_execution_options(
        workflow_name="Boss Review",
        trace_metadata={
            "surface": "review_api",
            "target_kind": material.target_kind,
            "project_path": material.project_path,
        },
    )
    result = await Runner.run(
        agent,
        input=_review_prompt(material),
        run_config=execution_options.run_config,
        session=execution_options.session,
    )

    final_output = result.final_output
    report = final_output if isinstance(final_output, ReviewReport) else ReviewReport.model_validate(final_output)
    record = normalize_review_record(report, material)
    save_review_record(record)
    return record


def normalize_review_record(report: ReviewReport, material: ReviewMaterial) -> ReviewRunRecord:
    findings: list[ReviewFinding] = []
    seen: set[tuple[str, str, str]] = set()
    base_path = material.repo_root or material.workspace_root

    for finding in report.findings:
        severity = str(finding.severity).strip().lower()
        if severity not in _SEVERITY_ORDER:
            severity = "medium"
        file_path = _normalize_output_path(finding.file_path, base_path)
        evidence = _clean_text(finding.evidence)
        risk = _clean_text(finding.risk)
        recommended_fix = _clean_text(finding.recommended_fix)
        if not evidence or not risk or not recommended_fix:
            continue
        if not file_path:
            file_path = material.changed_files[0] if len(material.changed_files) == 1 else (material.project_path or "unknown")
        key = (severity, file_path, evidence)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            ReviewFinding(
                severity=severity,
                file_path=file_path,
                evidence=evidence,
                risk=risk,
                recommended_fix=recommended_fix,
            )
        )

    findings.sort(key=lambda item: (_SEVERITY_ORDER[item.severity], item.file_path.lower(), item.evidence.lower()))
    severity_counts = {severity: 0 for severity in _SEVERITY_ORDER}
    for finding in findings:
        severity_counts[finding.severity] += 1

    summary = _clean_text(report.summary)
    if not summary:
        summary = "No actionable findings." if not findings else f"{len(findings)} actionable finding(s)."
    residual_risk = _clean_text(report.residual_risk)

    return ReviewRunRecord(
        review_id=uuid.uuid4().hex,
        created_at=_now_iso(),
        title=material.target_label,
        target_kind=material.target_kind,
        target_label=material.target_label,
        scope_summary=material.scope_summary,
        project_path=material.project_path,
        repo_root=str(material.repo_root) if material.repo_root else None,
        base_ref=material.base_ref,
        head_ref=material.head_ref,
        file_paths=list(material.changed_files),
        summary=summary,
        residual_risk=residual_risk,
        findings=findings,
        severity_counts={key: value for key, value in severity_counts.items() if value > 0},
    )


def save_review_record(record: ReviewRunRecord) -> Path:
    settings.review_history_dir.mkdir(parents=True, exist_ok=True)
    path = settings.review_history_dir / f"{record.review_id}.json"
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return path


def list_review_history(limit: int = 30) -> list[ReviewRunRecord]:
    directory = settings.review_history_dir
    if not directory.exists():
        return []

    records: list[ReviewRunRecord] = []
    for path in sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            records.append(ReviewRunRecord.model_validate_json(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        if len(records) >= limit:
            break
    return records


def load_review_record(review_id: str) -> ReviewRunRecord | None:
    path = settings.review_history_dir / f"{review_id}.json"
    if not path.exists():
        return None
    try:
        return ReviewRunRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _resolve_target_kind(requested_target: str, capabilities: dict[str, Any], file_paths: tuple[str, ...]) -> str:
    if requested_target != "auto":
        return requested_target
    if file_paths:
        return "files"
    return str(capabilities.get("default_target") or "files")


def _resolve_workspace_root(project_path: str | None) -> Path:
    if project_path:
        return Path(project_path).expanduser()
    return load_boss_control().root


def _resolve_indexed_project(store, workspace_root: Path, repo_root: Path | None) -> Project | None:
    candidates: list[Path] = []
    if repo_root is not None:
        candidates.extend([repo_root, repo_root.resolve(strict=False)])
    candidates.extend([workspace_root, workspace_root.resolve(strict=False)])

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        project = store.get_project(key)
        if project is not None:
            return project
    return None


def _project_summary_sections(store, project: Project | None) -> list[str]:
    if project is None:
        return []
    notes = _project_summary_notes(store, project.path)
    return [
        _clip_text(f"{note.title}\n{note.body}", _MAX_FILE_CHARS)
        for note in notes[:3]
    ]


def _project_summary_notes(store, project_path: str | None) -> list[ProjectNote]:
    if not project_path:
        return []
    notes = [note for note in store.list_project_summary_notes(limit=24) if note.project_path == project_path]
    if notes:
        return notes
    fallback = store.list_project_notes(project_path, limit=12)
    return [note for note in fallback if note.note_key == "overview" or note.category == "project_profile"]


def _collect_repo_file_contexts(repo_root: Path | None, changed_files: list[str]) -> list[tuple[str, str]]:
    if repo_root is None:
        return []
    absolute_paths = [repo_root / relative for relative in changed_files[:_MAX_CONTEXT_FILES]]
    return _collect_file_contexts(repo_root, tuple(str(path) for path in absolute_paths))


def _collect_file_contexts(base_root: Path, file_paths: tuple[str, ...]) -> list[tuple[str, str]]:
    contexts: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_path in file_paths:
        path = Path(raw_path).expanduser()
        candidate = path if path.is_absolute() else (base_root / path)
        resolved = candidate.resolve(strict=False)
        if str(resolved) in seen or not resolved.exists() or not resolved.is_file():
            continue
        if not is_path_allowed_for_agent(resolved):
            continue
        text = _read_text_file(resolved)
        if not text:
            continue
        seen.add(str(resolved))
        contexts.append((_display_path(resolved, base_root), _clip_text(text, _MAX_FILE_CHARS)))
        if len(contexts) >= _MAX_CONTEXT_FILES:
            break
    return contexts


def _docs_context(workspace_root: Path) -> str:
    control = load_boss_control(workspace_root)
    sections: list[str] = []
    if control.boss_md.strip():
        sections.append("BOSS.md\n" + _clip_text(control.boss_md.strip(), _MAX_DOC_CHARS // 3))
    if control.review.strip():
        sections.append(".boss/review.md\n" + _clip_text(control.review.strip(), _MAX_DOC_CHARS // 3))
    rules = applicable_rules(agent_name="code", mode="review", workspace_root=control.root)
    if rules:
        rule_text = "\n\n".join(f"{rule.title}\n{rule.body.strip()}" for rule in rules if rule.body.strip())
        sections.append("Applicable Boss Rules\n" + _clip_text(rule_text, _MAX_DOC_CHARS // 2))
    return "\n\n".join(section for section in sections if section.strip())


def _review_prompt(material: ReviewMaterial) -> str:
    sections = [
        "Review this local project evidence and return only substantive findings.",
        "Review requirements:\n- Findings first, ordered by severity\n- No style-only nits unless they hide a real bug\n- Use only evidence grounded in the provided local material\n- If evidence is insufficient, omit the finding and capture residual risk instead",
        f"Target\n- Kind: {material.target_kind}\n- Label: {material.target_label}\n- Scope: {material.scope_summary}",
    ]

    if material.diff_stat:
        sections.append("Diff Summary\n" + material.diff_stat)
    if material.diff_text:
        sections.append("Primary Diff Evidence\n```diff\n" + material.diff_text + "\n```")
    if material.file_contexts:
        sections.append(
            "File Context\n" + "\n\n".join(
                f"File: {path}\n```text\n{content}\n```" for path, content in material.file_contexts
            )
        )
    if material.project_summaries:
        sections.append("Indexed Project Context\n" + "\n\n".join(material.project_summaries))
    if material.docs_context:
        sections.append("Boss Review Docs\n" + material.docs_context)

    return "\n\n".join(section for section in sections if section.strip())


def _target_label(
    target_kind: str,
    workspace_root: Path,
    repo_root: Path | None,
    base_ref: str | None,
    head_ref: str | None,
    changed_files: list[str],
) -> str:
    project_name = (repo_root or workspace_root).name
    if target_kind == "working_tree":
        return f"Working tree review for {project_name}"
    if target_kind == "staged":
        return f"Staged diff review for {project_name}"
    if target_kind == "branch_diff":
        return f"Branch diff review for {project_name}: {base_ref}...{head_ref}"
    if target_kind == "project_summary":
        return f"Project summary review for {project_name}"
    if changed_files:
        preview = ", ".join(changed_files[:2])
        suffix = "" if len(changed_files) <= 2 else ", ..."
        return f"File review for {project_name}: {preview}{suffix}"
    return f"File review for {project_name}"


def _scope_summary(
    target_kind: str,
    repo_root: Path | None,
    current_branch: str | None,
    base_ref: str | None,
    head_ref: str | None,
    changed_files: list[str],
) -> str:
    if target_kind == "branch_diff":
        return f"Branch diff {base_ref}...{head_ref} in {repo_root or ''}".strip()
    if target_kind == "working_tree":
        return f"Current working tree on {current_branch or 'unknown branch'} with {len(changed_files)} changed file(s)"
    if target_kind == "staged":
        return f"Current staged diff on {current_branch or 'unknown branch'} with {len(changed_files)} file(s)"
    if target_kind == "project_summary":
        return "Indexed project summary fallback"
    return f"Specific files: {', '.join(changed_files[:4])}" if changed_files else "Specific file fallback"


def _normalize_output_path(raw_path: str, base_path: Path) -> str:
    cleaned = raw_path.strip()
    if not cleaned or cleaned == ".":
        return ""
    candidate = Path(cleaned)
    if candidate.is_absolute():
        return _display_path(candidate, base_path)
    return cleaned.replace("\\", "/")


def _display_path(path: Path, base_path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(base_path.resolve(strict=False)).as_posix()
    except ValueError:
        return path.as_posix()


def _clean_text(text: str) -> str:
    return " ".join(str(text).strip().split())


def _clip_text(text: str, limit: int) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 3].rstrip() + "..."


def _read_text_file(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if b"\x00" in data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("latin-1")
        except UnicodeDecodeError:
            return ""


def _git_root(path: Path) -> Path | None:
    output = _git_output(path, ["rev-parse", "--show-toplevel"])
    return Path(output).resolve(strict=False) if output else None


def _git_current_branch(repo_root: Path | None) -> str | None:
    if repo_root is None:
        return None
    return _git_output(repo_root, ["branch", "--show-current"])


def _git_name_only(repo_root: Path | None, args: list[str]) -> list[str]:
    if repo_root is None:
        return []
    output = _git_output(repo_root, args)
    if not output:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def _git_output(cwd: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()