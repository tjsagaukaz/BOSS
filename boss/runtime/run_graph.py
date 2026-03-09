from __future__ import annotations

import os
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Callable

from boss.runtime.file_leases import FileLeaseManager
from boss.runtime.planning_spine import PlanningSpine
from boss.types import StepExecutionResult


@dataclass
class RunNode:
    id: str
    step_index: int
    agent_role: str
    action: str
    dependencies: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    retry_budget: int = 0
    status: str = "pending"
    attempts: int = 0
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class RunGraph:
    nodes: dict[str, RunNode]
    completed: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)

    @classmethod
    def from_nodes(cls, nodes: list[RunNode]) -> "RunGraph":
        return cls(nodes={node.id: node for node in nodes})

    def ready_nodes(self) -> list[RunNode]:
        ready: list[RunNode] = []
        for node in self.nodes.values():
            if node.status != "pending":
                continue
            if any(dependency in self.failed for dependency in node.dependencies):
                node.status = "blocked"
                continue
            if all(dependency in self.completed for dependency in node.dependencies):
                ready.append(node)
        return sorted(ready, key=lambda item: item.step_index)

    def mark_running(self, node_id: str) -> None:
        self.nodes[node_id].status = "running"

    def mark_complete(self, node_id: str) -> None:
        self.completed.add(node_id)
        self.nodes[node_id].status = "completed"

    def mark_pending(self, node_id: str) -> None:
        self.nodes[node_id].status = "pending"

    def mark_failed(self, node_id: str) -> None:
        self.failed.add(node_id)
        self.nodes[node_id].status = "failed"

    def as_payload(self) -> dict[str, object]:
        return {
            "nodes": [
                {
                    "id": node.id,
                    "step_index": node.step_index,
                    "agent_role": node.agent_role,
                    "action": node.action,
                    "dependencies": list(node.dependencies),
                    "allowed_paths": list(node.allowed_paths),
                    "expected_outputs": list(node.expected_outputs),
                    "retry_budget": node.retry_budget,
                    "attempts": node.attempts,
                    "status": node.status,
                    "metadata": dict(node.metadata),
                }
                for node in sorted(self.nodes.values(), key=lambda item: item.step_index)
            ],
            "completed": sorted(self.completed),
            "failed": sorted(self.failed),
        }


def spine_to_run_graph(spine: PlanningSpine, retry_budget: int = 0) -> RunGraph:
    nodes: list[RunNode] = []
    for step in spine.steps:
        contract = step.contract
        step_id = contract.step_id or f"S{step.index + 1}"
        nodes.append(
            RunNode(
                id=step_id,
                step_index=step.index,
                agent_role=contract.agent_role or "engineer",
                action=contract.objective or contract.title,
                dependencies=list(contract.dependencies),
                allowed_paths=list(contract.allowed_paths),
                expected_outputs=list(contract.expected_outputs or contract.required_artifacts),
                retry_budget=retry_budget,
                metadata={"title": contract.title},
            )
        )
    return RunGraph.from_nodes(nodes)


class RunGraphExecutor:
    def __init__(
        self,
        graph: RunGraph,
        node_runner: Callable[[RunNode, list[StepExecutionResult]], StepExecutionResult],
        *,
        parallel: bool | None = None,
        max_workers: int | None = None,
        on_event: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.graph = graph
        self.node_runner = node_runner
        self.parallel = self._parallel_enabled() if parallel is None else parallel
        self.max_workers = max_workers or max(1, min(4, len(graph.nodes)))
        self.on_event = on_event
        self.leases = FileLeaseManager()
        self.results: dict[str, StepExecutionResult] = {}
        self.terminal_node_id: str | None = None
        self.terminal_status: str | None = None

    def run(self) -> None:
        if self.parallel:
            self._run_parallel()
            return
        self._run_sequential()

    def ordered_results(self) -> list[StepExecutionResult]:
        return [
            self.results[node.id]
            for node in sorted(self.graph.nodes.values(), key=lambda item: item.step_index)
            if node.id in self.results
        ]

    def _run_sequential(self) -> None:
        while True:
            ready = self.graph.ready_nodes()
            if not ready or self.terminal_node_id is not None:
                break
            node = self._acquire_next_ready_node(ready)
            if node is None:
                raise RuntimeError("Run graph deadlocked while waiting on file leases.")
            result = self._execute_node(node)
            self.leases.release(node.id)
            self._handle_result(node, result)

    def _run_parallel(self) -> None:
        running: dict[Future[StepExecutionResult], RunNode] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while True:
                if self.terminal_node_id is None:
                    ready = self.graph.ready_nodes()
                    for node in ready:
                        if len(running) >= self.max_workers:
                            break
                        if not self.leases.acquire(node.id, node.allowed_paths):
                            continue
                        self.graph.mark_running(node.id)
                        node.attempts += 1
                        completed_snapshot = self._completed_results()
                        self._emit(
                            {
                                "event": "node_started",
                                "node_id": node.id,
                                "step_index": node.step_index,
                                "agent_role": node.agent_role,
                            }
                        )
                        running[executor.submit(self.node_runner, node, completed_snapshot)] = node

                if not running:
                    break

                done, _ = wait(running.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    node = running.pop(future)
                    self.leases.release(node.id)
                    result = future.result()
                    self._handle_result(node, result)

                if self.terminal_node_id is not None and not running:
                    break

    def _acquire_next_ready_node(self, ready_nodes: list[RunNode]) -> RunNode | None:
        for node in ready_nodes:
            if not self.leases.acquire(node.id, node.allowed_paths):
                continue
            return node
        return None

    def _execute_node(self, node: RunNode) -> StepExecutionResult:
        self.graph.mark_running(node.id)
        node.attempts += 1
        self._emit(
            {
                "event": "node_started",
                "node_id": node.id,
                "step_index": node.step_index,
                "agent_role": node.agent_role,
            }
        )
        return self.node_runner(node, self._completed_results())

    def _handle_result(self, node: RunNode, result: StepExecutionResult) -> None:
        self.results[node.id] = result
        if result.status == "completed":
            self.graph.mark_complete(node.id)
            self._emit(
                {
                    "event": "node_completed",
                    "node_id": node.id,
                    "step_index": node.step_index,
                    "agent_role": node.agent_role,
                }
            )
            return

        if result.status != "stopped" and node.retry_budget > 0:
            node.retry_budget -= 1
            self.graph.mark_pending(node.id)
            self._emit(
                {
                    "event": "node_retry",
                    "node_id": node.id,
                    "step_index": node.step_index,
                    "agent_role": node.agent_role,
                    "retry_budget": node.retry_budget,
                }
            )
            return

        self.graph.mark_failed(node.id)
        self.terminal_node_id = node.id
        self.terminal_status = result.status
        self._emit(
            {
                "event": "node_failed",
                "node_id": node.id,
                "step_index": node.step_index,
                "agent_role": node.agent_role,
                "status": result.status,
            }
        )

    def _completed_results(self) -> list[StepExecutionResult]:
        results: list[StepExecutionResult] = []
        for node_id in sorted(self.graph.completed, key=lambda item: self.graph.nodes[item].step_index):
            result = self.results.get(node_id)
            if result is not None:
                results.append(result)
        return results

    def _emit(self, payload: dict[str, object]) -> None:
        if self.on_event is None:
            return
        self.on_event(payload)

    def _parallel_enabled(self) -> bool:
        value = os.getenv("RUN_GRAPH_PARALLEL", "")
        return value.strip().lower() in {"1", "true", "yes", "on"}
