"""policies x tasks x repeats -> results/runs.jsonl (resumable; one record per run)."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import anthropic

from gateway.loader import load_policies, load_tasks
from harness.env import load_env
from harness.session import run_task

RESULTS = Path(__file__).resolve().parent.parent / "results" / "runs.jsonl"
ATTEMPTS = 4


def select(arg: str, available: list[str], what: str) -> list[str]:
    if arg == "all":
        return available
    chosen = [a.strip() for a in arg.split(",") if a.strip()]
    unknown = [c for c in chosen if c not in available]
    if unknown:
        raise SystemExit(f"unknown {what}: {unknown}; available: {available}")
    return chosen


def done_run_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["run_id"] for line in path.read_text().splitlines() if line.strip()}


def run_with_backoff(client: anthropic.Anthropic, policy: str, task: dict[str, Any], repeat: int,
                     policies: dict[str, Any]) -> dict[str, Any]:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return run_task(client, policy, task, repeat, policies=policies)
        except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError) as e:
            if attempt == ATTEMPTS:
                raise
            delay = 2 ** attempt * 5
            print(f"[retry] {policy}:{task['id']}:{repeat} attempt {attempt} failed ({type(e).__name__}); "
                  f"sleeping {delay}s", file=sys.stderr)
            time.sleep(delay)
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policies", default="all")
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=str(RESULTS))
    args = ap.parse_args(argv)
    load_env()

    policies = load_policies()
    tasks = {t["id"]: t for t in load_tasks()}
    pol_names = select(args.policies, list(policies), "policy")
    task_ids = select(args.tasks, list(tasks), "task")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = done_run_ids(out)
    jobs = [(p, tid, r) for p in pol_names for tid in task_ids for r in range(args.repeats)
            if f"{p}:{tid}:{r}" not in done]
    print(f"{len(jobs)} runs to do ({len(done)} already in {out})", file=sys.stderr)

    client = anthropic.Anthropic(max_retries=6)
    lock, failures = threading.Lock(), []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_with_backoff, client, p, tasks[tid], r, policies): (p, tid, r) for p, tid, r in jobs}
        for n, fut in enumerate(as_completed(futs), start=1):
            job = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:  # not written, so a re-run retries it
                failures.append((job, repr(e)))
                print(f"[FAIL] {job}: {e!r}", file=sys.stderr)
                continue
            with lock, out.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"[{n}/{len(jobs)}] {rec['run_id']}", file=sys.stderr)
    if failures:
        print(f"{len(failures)} runs failed; re-run to retry them", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
