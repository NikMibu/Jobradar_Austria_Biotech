# scripts/pod

Helpers for running the full heimspiel pipeline on a disposable Runpod GPU pod
with a local Ollama model (zero API cost). Not wired into the CLI — copied onto
the pod by hand.

| file | role |
|---|---|
| `pod_setup.sh` | Runs detached on a fresh pod: installs uv, starts `ollama serve`, pulls `qwen3.8:27b`, clones the repo + checks out the working branch, `uv sync`. Writes `/root/setup.DONE` or `/root/setup.FAIL`. |
| `run_pipeline.sh` | Runs all pipeline stages detached (`extract → locations → companies --geocode → travel → score → export → report`), one `/root/pipe.<stage>.DONE` marker per stage, `/root/pipe.ALL.DONE` at the end, `/root/pipe.FAIL` on a stage failure. Survives the controlling SSH/Claude session dying. |
| `mon.py` | Ad-hoc progress probe — counts extracted postings / scores / located sites / travel times against the DB. `python mon.py top` also prints the current top matches. |
| `scprobe.py` | One-off: times `score_one()` on a few postings to sanity-check the scoring model. |

## Typical run

```sh
# on the pod, after scp'ing these over:
export HEIMSPIEL_LLM=ollama
nohup bash pod_setup.sh &        # wait for /root/setup.DONE
nohup bash run_pipeline.sh &     # poll /root/pipe.*.DONE
```

Pull back afterwards (all gitignored): `data/heimspiel.db`,
`site/public/data/{jobs,companies,meta}.json`, `data/report-<date>.md`.
A full run is roughly 4–5 h on an L40S — terminate the pod when done.
