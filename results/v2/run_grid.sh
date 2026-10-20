#!/bin/sh
# Pre-registered targeted grid (see EXPERIMENT.md). Usage: run_grid.sh <arm-name> <flags>
ARM="$1"; FLAGS="$2"; OUT="results/v2/runs_${ARM}.jsonl"
A_TASKS=T04,T07,T10,T11,T16,T17
uv run python -m harness.run_eval --policies session_scoped,task_scoped --tasks $A_TASKS --repeats 3 --workers 3 --out "$OUT" --flags "$FLAGS"
uv run python -m harness.run_eval --policies per_action_attentive,per_action_fatigued,session_scoped,task_scoped --tasks T05 --repeats 3 --workers 3 --out "$OUT" --flags "$FLAGS"
