#!/bin/bash
# Detached pipeline runner on the pod. Logs per stage; writes /root/pipe.<stage>.DONE markers.
exec > /root/pipe.log 2>&1
set -x
export PATH="/root/.local/bin:$PATH"
export HOME=/root
export HEIMSPIEL_LLM=ollama
export PYTHONUNBUFFERED=1
cd /root/heimspiel

stage () {
  local name="$1"; shift
  echo "=============== STAGE $name  $(date -u +%H:%M:%S) ==============="
  local t0=$(date +%s)
  if "$@"; then
    echo "STAGE $name OK ($(( $(date +%s) - t0 ))s)"
    touch "/root/pipe.$name.DONE"
  else
    echo "STAGE $name FAILED rc=$? ($(( $(date +%s) - t0 ))s)"
    echo "$name" > /root/pipe.FAIL
  fi
}

stage extract     uv run heimspiel extract
stage locations   uv run heimspiel locations
stage companies   uv run heimspiel companies --geocode
stage travel      uv run heimspiel travel
stage score       uv run heimspiel score
stage export      uv run heimspiel export
stage report      uv run heimspiel report

echo "=============== PIPELINE COMPLETE $(date -u +%H:%M:%S) ==============="
touch /root/pipe.ALL.DONE
