"""Offline, isolated measurement of actual main-agent construction."""

from __future__ import annotations

import argparse
import cProfile
import json
import os
import pstats
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--baseline", type=Path, help="Alternate with an unmodified source tree"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument(
        "--provider", choices=["moonshot", "dashscope"], default="moonshot"
    )
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if args.samples < 1:
        parser.error("--samples must be positive")
    for tree in [source, *([args.baseline.resolve()] if args.baseline else [])]:
        if not (tree / "jw" / "agent.py").is_file():
            parser.error(f"Not a JW source tree: {tree}")
    output.mkdir(parents=True, exist_ok=True)
    if not args.child:
        for index in range(args.samples):
            sources = [("candidate", source)]
            if args.baseline:
                sources.insert(0, ("baseline", args.baseline.resolve()))
                if index % 2:
                    sources.reverse()
            for label, tree in sources:
                run_sample(args, tree, output / label / f"sample-{index}")
        if args.baseline:
            summarize(output, args.samples)
        return

    def prohibit_network(event, _args):
        if event in {"socket.connect", "socket.getaddrinfo"}:
            raise RuntimeError("Network access is disabled in this benchmark")

    sys.addaudithook(prohibit_network)
    profile = cProfile.Profile()
    start = time.perf_counter()
    if args.profile:
        profile.enable()
    import jw
    from jw.langgraph_dev.main_graph import jw_agent

    if Path(jw.__file__).resolve().parent != source / "jw":
        raise RuntimeError("Benchmark imported JW from a different source tree")

    graph = jw_agent()
    profile.disable()
    elapsed = time.perf_counter() - start
    if args.profile:
        profile.dump_stats(str(output / "construction.prof"))
        with (output / "profile.txt").open("w", encoding="utf-8") as stream:
            pstats.Stats(profile, stream=stream).sort_stats("cumulative").print_stats(
                90
            )
    tools = graph.nodes["tools"].bound.tools_by_name
    from langchain_core.utils.function_calling import convert_to_openai_tool

    interface = {name: convert_to_openai_tool(tool) for name, tool in tools.items()}
    (output / "interface.json").write_text(
        json.dumps(interface, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output / "timing.json").write_text(
        json.dumps(
            {
                "construction_seconds": elapsed,
                "graph_type": type(graph).__name__,
                "tool_count": len(tools),
                "source": str(source),
                "provider": args.provider,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def run_sample(args, source, sample):
    with tempfile.TemporaryDirectory(prefix="jw-startup-") as isolated:
        root = Path(isolated)
        config = root / "config" / "jw"
        config.mkdir(parents=True)
        model = "kimi-k2.6" if args.provider == "moonshot" else "qwen3.7-plus"
        (config / "config.yaml").write_text(
            f"provider: {args.provider}\nmodel: {model}\n"
            f"{args.provider}_api_key: synthetic-offline-benchmark\n",
            encoding="utf-8",
        )
        env = {
            key: os.environ[key]
            for key in (
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "COMSPEC",
                "PATHEXT",
                "LD_LIBRARY_PATH",
                "VIRTUAL_ENV",
            )
            if key in os.environ
        }
        env.update(
            {
                "HOME": str(root),
                "USERPROFILE": str(root),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_CACHE_HOME": str(root / "cache"),
                "JW_DATA_DIR": str(root / "data"),
                "JW_WORKSPACE_DIR": str(root / "workspace"),
                "JW_PROJECT_ROOT": str(source),
                "PYTHONPATH": os.pathsep.join((str(source), str(source / "src"))),
                "PYTHON_DOTENV_DISABLED": "1",
                "PYTHONUTF8": "1",
                "JW_DEPLOY_MODE": "full",
                "JW_KB_AUTO_GROWTH": "0",
                "LANGSMITH_TRACING": "false",
                "LANGCHAIN_TRACING_V2": "false",
            }
        )
        sample.mkdir(parents=True, exist_ok=True)
        with (sample / "process.log").open("w", encoding="utf-8") as log:
            subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--source",
                    str(source),
                    "--output",
                    str(sample),
                    "--child",
                    "--provider",
                    args.provider,
                    *(["--profile"] if args.profile else []),
                ],
                env=env,
                cwd=root,
                stdout=log,
                stderr=log,
                check=True,
                timeout=120,
            )
        print((sample / "timing.json").read_text(encoding="utf-8"), flush=True)


def summarize(output: Path, samples: int):
    result = {}
    reference = None
    for label in ("baseline", "candidate"):
        timings = []
        for index in range(samples):
            sample = output / label / f"sample-{index}"
            timings.append(
                json.loads((sample / "timing.json").read_text(encoding="utf-8"))[
                    "construction_seconds"
                ]
            )
            interface = json.loads(
                (sample / "interface.json").read_text(encoding="utf-8")
            )
            if reference is None:
                reference = interface
            if interface != reference:
                raise ValueError(f"Tool interface changed: {label} sample {index}")
        result[label] = {
            "samples_seconds": timings,
            "median_seconds": statistics.median(timings),
        }
    result["tool_interfaces_equal"] = True
    result["tool_count"] = len(reference)
    result["median_reduction_percent"] = 100 * (
        1 - result["candidate"]["median_seconds"] / result["baseline"]["median_seconds"]
    )
    (output / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
