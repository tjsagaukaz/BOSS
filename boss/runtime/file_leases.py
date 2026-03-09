from __future__ import annotations

from pathlib import Path


class FileLeaseManager:
    GLOBAL_LEASE = "*"

    def __init__(self) -> None:
        self.active_leases: dict[str, str] = {}

    def acquire(self, node_id: str, paths: list[str]) -> bool:
        normalized_paths = self._normalize_paths(paths)
        if self.GLOBAL_LEASE in self.active_leases and self.active_leases[self.GLOBAL_LEASE] != node_id:
            return False
        if self.GLOBAL_LEASE in normalized_paths and self.active_leases:
            return False
        for path in normalized_paths:
            owner = self.active_leases.get(path)
            if owner is not None and owner != node_id:
                return False
            if path == self.GLOBAL_LEASE:
                if any(owner_id != node_id for owner_id in self.active_leases.values()):
                    return False
                continue
            for leased_path, owner_id in self.active_leases.items():
                if owner_id == node_id:
                    continue
                if self._paths_conflict(path, leased_path):
                    return False

        for path in normalized_paths:
            self.active_leases[path] = node_id
        return True

    def release(self, node_id: str) -> None:
        for path in [path for path, owner in self.active_leases.items() if owner == node_id]:
            self.active_leases.pop(path, None)

    def _normalize_paths(self, paths: list[str]) -> list[str]:
        cleaned_paths: list[str] = []
        for path in paths:
            cleaned = str(path).strip()
            if not cleaned:
                continue
            normalized = cleaned.lstrip("./")
            if normalized:
                cleaned_paths.append(normalized)
        if not cleaned_paths:
            return [self.GLOBAL_LEASE]
        return list(dict.fromkeys(cleaned_paths))

    def _paths_conflict(self, left: str, right: str) -> bool:
        if self.GLOBAL_LEASE in {left, right}:
            return True
        left_path, left_is_dir = self._path_parts(left)
        right_path, right_is_dir = self._path_parts(right)
        if left_path == right_path:
            return True
        if left_is_dir and (right_path == left_path or right_path.startswith(f"{left_path}/")):
            return True
        if right_is_dir and (left_path == right_path or left_path.startswith(f"{right_path}/")):
            return True
        return False

    def _path_parts(self, path: str) -> tuple[str, bool]:
        raw = str(path).strip().lstrip("./")
        cleaned = raw.rstrip("/")
        path_obj = Path(cleaned)
        is_dir = raw.endswith("/") or ("." not in path_obj.name)
        return cleaned, is_dir
