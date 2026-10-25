#!/bin/sh
# Sobe o sala.py no Railway. Variáveis:
#   SALA_ARGS      padrão "--mode live" (ensaio: "--mode mock --scenario swing2022 --speed 20")
#   CLEAN_LIVE_ID  mudar o valor (ex.: 1630) faz UMA subida com --clean-live; reinícios seguintes não limpam.
set -e
mkdir -p /app/data/live /app/data/site-live
rm -rf /app/data/base && cp -r /app/_base /app/data/base
rm -f /app/data/live/logs/sala.pids.json  # pids de outro contêiner não significam nada aqui
ARGS="${SALA_ARGS:---mode live} --lan --port ${PORT:-8080}"
if [ -n "$CLEAN_LIVE_ID" ] && [ "$CLEAN_LIVE_ID" != "$(cat /app/data/.clean_live_id 2>/dev/null)" ]; then
  echo "$CLEAN_LIVE_ID" > /app/data/.clean_live_id
  ARGS="$ARGS --clean-live"
fi
echo "[entrypoint] python sala.py $ARGS"
exec python sala.py $ARGS
