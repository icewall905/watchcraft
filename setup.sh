#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source scripts/common.sh
require_docker
if [ ! -f .env ]; then
  echo 'BetterTube setup. Press Enter to use the defaults.'
  while true; do
    read -r -p 'Folder for app data [./data]: ' data
    read -r -p 'Folder for downloaded videos [./media]: ' media
    data=${data:-./data}; media=${media:-./media}
    # Expand the familiar ~/ form without evaluating user input as shell code.
    case "$data" in '~/'*) data="$HOME/${data:2}" ;; esac
    case "$media" in '~/'*) media="$HOME/${media:2}" ;; esac
    mkdir -p "$data" "$media" || fail 'Could not create the folders. Choose folders your user can write to.'
    data=$(cd "$data" && pwd -P); media=$(cd "$media" && pwd -P)
    case "$PWD/" in "$data/"*|"$media/"*) echo 'Choose dedicated storage folders, not the repository or one of its parents.'; continue ;; esac
    case "$data/" in "$media/"*) echo 'App data and videos need separate, non-nested folders.'; continue ;; esac
    case "$media/" in "$data/"*) echo 'App data and videos need separate, non-nested folders.'; continue ;; esac
    [ -w "$data" ] && [ -w "$media" ] || fail 'Both folders must be writable by your user.'
    break
  done
  while true; do
    read -r -p 'Web port [6645]: ' port
    port=${port:-6645}
    if [[ "$port" =~ ^[0-9]{1,5}$ ]] && (( 10#$port >= 1 && 10#$port <= 65535 )); then port=$((10#$port)); break; fi
    echo 'Choose a number from 1 to 65535.'
  done
  # Double quoting handles trailing backslashes; $$ keeps dollar signs literal in Compose.
  quote() {
    local value=$1
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    value=${value//\$/\$\$}
    printf '"%s"' "$value"
  }
  umask 077
  {
    printf 'BT_DATA_HOST_DIR='; quote "$data"; printf '\nBT_MEDIA_HOST_DIR='; quote "$media"
    printf '\nBT_PORT=%s\nBT_AUTH=accounts' "$port"
    printf '\nBT_SECRET=\nBT_LLM_BASE_URL=\nBT_LLM_MODEL=\nBT_LLM_API_KEY=\nBT_POT_URL=http://pot:4416\nBT_YTDLP_AUTOUPDATE=1\n'
  } > .env
  echo 'Saved configuration to .env. More settings are available in the web app.'
else
  echo 'Using your existing .env. Your sign-in mode and storage choices are preserved.'
fi
if [ "${1:-}" = '--configure-only' ]; then
  compose_check
  echo 'Configuration saved. You can now restore a backup with bash manage.sh restore.'
else
  start_app
  echo 'Open the site. On a fresh install the sign-in screen offers "create the first admin".'
  echo 'On an existing install, create an invite link for each person (add --admin for administrators):'
  echo '  docker compose exec bt-api python -m bt.accounts_cli invite <username> [--admin]'
fi
