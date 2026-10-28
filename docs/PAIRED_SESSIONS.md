# Paired agent-task evaluation (not yet run)

This is an executable **measurement protocol**, not evidence that SBS saves
money or improves coding. The repository currently has synthetic filter tests;
it does **not** contain provider-billed paired coding sessions or success-rate
results. No API calls or paid agent tasks are initiated by this script.

## Trial design

1. Freeze a set of actual programming tasks and their repositories at exact
   commits. State an independent acceptance test for each task.
2. Run each task with the same model, prompt, allowed tools, time budget and
   clean repository snapshot under two conditions: control (SBS disabled) and
   sbs (enabled). Randomize order and repeat each task at least five times.
3. Prevent state sharing across trials. Record model, task revision and
   repository SHA. Document tool/runtime versions and caching conditions.
4. Collect **provider-reported** total tokens and billed costs where exposed;
   separate input/output/cache tokens when the provider makes them available.
   Record tool calls, SBS restoration calls, wall time, test success and errors.
   Leave unavailable metrics null, never use character counts as token counts.
5. Assess correctness against the frozen task acceptance tests, including
   regressions. Save redacted, nonsecret traces. Report **every** task, failures
   and inconclusive cases. A reduction in tool output is not a productivity gain.

## JSONL record and analysis

One line per task, repetition and condition:

```json
{"task_id":"fix-case-01","repeat":0,"variant":"control","success":true,"model":"MODEL-ID","task_revision":"v1","repository_sha":"COMMIT-SHA","tokens_total":null,"cost_usd":null,"tool_calls":null,"restore_calls":null,"elapsed_seconds":null,"errors":null}
```

This is a **schema illustration**, not an observed trial.

```sh
python scripts/paired_sessions.py /path/to/redacted-real-trials.jsonl
python scripts/paired_sessions.py /path/to/redacted-real-trials.jsonl --output /path/to/report.json
python -m unittest tests.test_paired_sessions -v
```

The script validates inputs, pairs by task and repetition, refuses conflicting
model/commit metadata, excludes unmatched trials **and any complete pair missing
model, task revision or repository SHA** from paired comparisons. Pairs that
claim different values for those fields fail with an error. Missing values
remain `null`, not zero. The report lists excluded pairs explicitly.
The output warns that task equivalence and reported metrics were not independently
verified. It does not calculate p-values or infer causation from tiny samples.

## Evidence still needed

A genuine paired study with independently verified task outcomes and provider
billing is not included. Current SBS benchmark results are synthetic regression
measurements only. Real-agent execution may consume paid quotas and must be
authorized separately. Do not publish raw traces containing private files, tokens,
API credentials or personal information.
