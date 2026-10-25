#!/bin/sh
# Monta uma pasta com o código do git + a base do 1T (fora do git) + cache do simulador e sobe no serviço "sala".
#   sh cloud/deploy_sala.sh
set -e
cd "$(dirname "$0")/.."
STAGE="${TMPDIR:-/tmp}/apuracao-sala-stage"
rm -rf "$STAGE" && mkdir -p "$STAGE"
git ls-files -z | grep -zv '^\.dockerignore$' | xargs -0 -I{} rsync -R "{}" "$STAGE/"
rsync -R -a data/base site/data/base mock/cache "$STAGE/"
printf '.git\n**/__pycache__\n.venv\n' > "$STAGE/.dockerignore"
du -sh "$STAGE"
railway up "$STAGE" --path-as-root --no-gitignore --project 1f42035d-81b7-4eaf-9705-f09ec704b0f3 --environment production --service sala --ci -m "${1:-sala completa}"
