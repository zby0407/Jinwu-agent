#!/usr/bin/env python3
"""Seed one immutable producer artifact for a focused real-WebUI Evidence probe.

The formal campaign never calls this helper.  It is only for the two bounded
Evidence acceptance probes where the input report is already the producer
artifact under review and rerunning Planner/Data/Experiment would test the
wrong component.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jw.research_review import ResearchReviewStore
from jw.workspaces import ensure_thread_workspace


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("thread_id")
    parser.add_argument("case_id")
    parser.add_argument("input_files", nargs="+")
    args = parser.parse_args()

    repository = Path(__file__).resolve().parents[3]
    binding = ensure_thread_workspace(args.thread_id, repository)
    workspace = Path(binding.workspace)
    refs: list[str] = []
    excerpts: list[str] = []
    for name in args.input_files:
        source = workspace / "inputs" / Path(name).name
        if not source.is_file():
            raise FileNotFoundError(f"uploaded probe input is missing: {source.name}")
        ref = source.relative_to(workspace).as_posix()
        refs.append(ref)
        excerpts.append(f"[{ref}]\n{source.read_text(encoding='utf-8')}")

    store = ResearchReviewStore(workspace, args.thread_id)
    if store.latest_artifact("data") is not None:
        raise RuntimeError("the focused probe task already has a Data artifact")
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        phase="bounded_data",
        content=(
            f"Focused Evidence probe {args.case_id}. The following uploaded report "
            "is the immutable producer result to review; do not infer support from "
            "this wrapper.\n"
            + "\n\n".join(excerpts)
        ),
    )
    print(
        json.dumps(
            {
                "schema_version": "evidence-probe-seed-v1",
                "case_id": args.case_id,
                "thread_id": args.thread_id,
                "stage": artifact["stage"],
                "artifact_id": artifact["artifact_id"],
                "artifact_version": artifact["version"],
                "source_refs": refs,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
