#!/bin/bash
# Runs detached on the pod. Logs to /root/setup.log. Writes /root/setup.DONE or /root/setup.FAIL at the end.
set -x
exec > /root/setup.log 2>&1
rc=0

export DEBIAN_FRONTEND=noninteractive
export HOME=/root
export PATH="/root/.local/bin:$PATH"

# 1. uv
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh || rc=1
fi
export PATH="/root/.local/bin:$PATH"
uv --version || rc=1

# 2. ollama serve (detached, survives this script)
if ! pgrep -x ollama >/dev/null; then
  setsid bash -c 'OLLAMA_HOST=127.0.0.1:11434 OLLAMA_KEEP_ALIVE=30m ollama serve' > /root/ollama.log 2>&1 < /dev/null &
fi
for i in $(seq 1 30); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 2; done
curl -sf http://127.0.0.1:11434/api/tags >/dev/null || rc=1

# 3. pull the model  (THIS verifies the qwen3.8:27b tag)
echo "=== pulling qwen3.8:27b ==="
if ! ollama pull qwen3.8:27b; then
  echo "MODEL_PULL_FAILED qwen3.8:27b"
  rc=2
fi

# 4. clone repo + checkout branch + sync
if [ ! -d /root/heimspiel ]; then
  git clone https://github.com/NikMibu/Jobradar_Austria_Biotech /root/heimspiel || rc=1
fi
cd /root/heimspiel
git fetch -q origin && git checkout feature/neues-ranking && git pull -q --ff-only || rc=1
uv sync || rc=1

echo "=== ollama list ==="
ollama list

if [ "$rc" = "0" ]; then touch /root/setup.DONE; else echo "rc=$rc" > /root/setup.FAIL; fi
echo "SETUP EXIT rc=$rc"
