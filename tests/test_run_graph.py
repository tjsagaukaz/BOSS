from __future__ import annotations

import time

from boss.runtime import FileLeaseManager, PlanningSpine, RunGraphExecutor, spine_to_run_graph
from boss.types import PlanStepContract, StepExecutionResult, StructuredPlan


def test_file_leases_block_overlapping_paths():
    leases = FileLeaseManager()

    assert leases.acquire("node-a", ["middleware/"])
    assert not leases.acquire("node-b", ["middleware/auth.py"])

    leases.release("node-a")

    assert leases.acquire("node-b", ["middleware/auth.py"])


def test_run_graph_sequential_respects_dependencies_and_order():
    plan = StructuredPlan(
        goal="Add logging",
        steps=["Create middleware", "Write tests", "Audit"],
        contracts=[
            PlanStepContract(title="Create middleware", step_id="S1", objective="Create middleware"),
            PlanStepContract(title="Write tests", step_id="S2", objective="Write tests"),
            PlanStepContract(title="Audit", step_id="S3", objective="Audit", dependencies=["S1", "S2"], agent_role="auditor"),
        ],
        raw_text="",
    )
    spine = PlanningSpine.from_plan(task_id="task-graph-1", plan=plan)
    graph = spine_to_run_graph(spine)
    order: list[str] = []

    def node_runner(node, completed_steps):
        order.append(node.id)
        return StepExecutionResult(
            step_index=node.step_index,
            step_title=node.action,
            status="completed",
            iterations=1,
        )

    executor = RunGraphExecutor(graph=graph, node_runner=node_runner, parallel=False)
    executor.run()

    assert order == ["S1", "S2", "S3"]
    assert [result.step_index for result in executor.ordered_results()] == [0, 1, 2]


def test_run_graph_parallel_runs_independent_nodes_concurrently():
    plan = StructuredPlan(
        goal="Add logging",
        steps=["Create middleware", "Write tests", "Audit"],
        contracts=[
            PlanStepContract(title="Create middleware", step_id="S1", objective="Create middleware", allowed_paths=["middleware/"]),
            PlanStepContract(title="Write tests", step_id="S2", objective="Write tests", allowed_paths=["tests/"], agent_role="test"),
            PlanStepContract(title="Audit", step_id="S3", objective="Audit", dependencies=["S1", "S2"], agent_role="auditor"),
        ],
        raw_text="",
    )
    spine = PlanningSpine.from_plan(task_id="task-graph-2", plan=plan)
    graph = spine_to_run_graph(spine)
    start_times: dict[str, float] = {}

    def node_runner(node, completed_steps):
        start_times[node.id] = time.perf_counter()
        time.sleep(0.05)
        return StepExecutionResult(
            step_index=node.step_index,
            step_title=node.action,
            status="completed",
            iterations=1,
        )

    executor = RunGraphExecutor(graph=graph, node_runner=node_runner, parallel=True, max_workers=2)
    executor.run()

    assert set(start_times) == {"S1", "S2", "S3"}
    assert abs(start_times["S1"] - start_times["S2"]) < 0.04
    assert start_times["S3"] >= max(start_times["S1"], start_times["S2"])
