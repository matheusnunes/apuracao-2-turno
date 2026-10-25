#!/usr/bin/env bash
# Mantém o coletor vivo mesmo se o processo morrer de fora (kill -9, OOM) e o Mac acordado.
# Ctrl-C / SIGTERM neste script encerram de vez. Uso: collector/run_forever.sh [flags do run]
cd "$(dirname "$0")/.." || exit 1
caffeinate -i -w $$ &
stop=0
trap 'stop=1; kill -TERM "$child" 2>/dev/null' INT TERM
while [ "$stop" = 0 ]; do
  uv run python -m collector run "$@" &
  child=$!
  wait "$child"; code=$?
  [ "$stop" = 1 ] && { wait "$child" 2>/dev/null; break; }
  echo "$(date '+%F %T') coletor saiu (código $code); reiniciando em 5 s" >&2
  sleep 5
done
