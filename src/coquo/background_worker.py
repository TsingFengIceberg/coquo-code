"""Foreground entry point for the restartable Coquo Child worker."""

from __future__ import annotations

import argparse
from pathlib import Path
from threading import Event

from coquo.background_runtime import (
    BACKGROUND_IDLE_SECONDS,
    MAX_BACKGROUND_WORKERS,
    PersistentChildWorker,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coquo-background-worker")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument(
        "--worker-count",
        type=int,
        default=MAX_BACKGROUND_WORKERS,
        choices=range(1, MAX_BACKGROUND_WORKERS + 1),
    )
    parser.add_argument("--idle-seconds", type=float, default=BACKGROUND_IDLE_SECONDS)
    parser.add_argument("--max-items", type=int)
    parser.add_argument("--recover-only", action="store_true")
    parser.add_argument(
        "--fleet-size",
        type=int,
        default=1,
        help="number of independently leased local workers (1-8)",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="keep supervising the durable queue until interrupted",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.fleet_size > 1 and arguments.daemon:
        raise SystemExit("--fleet-size and --daemon cannot be combined")
    if arguments.fleet_size > 1:
        from coquo.background_runtime import BackgroundWorkerFleet

        result = BackgroundWorkerFleet(
            arguments.workspace,
            fleet_size=arguments.fleet_size,
            worker_count=arguments.worker_count,
            idle_seconds=arguments.idle_seconds,
        ).run(max_items=arguments.max_items)
        return 0 if result.outcome != "failed" else 1
    worker = PersistentChildWorker(
        arguments.workspace,
        worker_count=arguments.worker_count,
        idle_seconds=arguments.idle_seconds,
    )
    result = (
        worker.recover_orphans()
        if arguments.recover_only
        else worker.run_forever(stop_event=Event())
        if arguments.daemon
        else worker.run(max_items=arguments.max_items)
    )
    return 0 if result.outcome != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
