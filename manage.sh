#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source scripts/common.sh
require_docker
[ -f .env ] || fail 'Run setup.sh first. For a restore, use bash setup.sh --configure-only.'
compose_check
action=${1:-}
if [ -z "$action" ]; then
  echo '1) Start  2) Update  3) Backup  4) Restore  5) Status  6) Stop  7) Reset profile PIN'
  read -r -p 'Choose an action: ' action
fi
make_backup() {
  mkdir -p backups
  local include=''
  read -r -p 'Include downloaded videos? This may be large [y/N]: ' videos
  case "$videos" in y|Y|yes|YES) include='--media' ;; esac
  # Restart exactly the services that were running before the backup, even on failure.
  running=$(docker compose ps --status running --services)
  # Also recover if the archive command is interrupted or fails unexpectedly.
  trap 'if [ -n "${running:-}" ]; then docker compose start $running; fi' EXIT
  if [ -n "$running" ]; then docker compose stop; fi
  local result=0
  helper -v "$PWD/backups:/backup" -v "$PWD/.env:/config.env:ro" bt-api - backup ${include:+"$include"} || result=$?
  if [ -n "$running" ]; then
    # Service names come from Compose, not user input.
    docker compose start $running || fail 'Backup finished, but restarting failed. Run setup again.'
  fi
  trap - EXIT
  [ "$result" = 0 ] || fail 'Backup failed. Your original files are still in place.'
  echo "Backup is in $PWD/backups. Copy it to another drive for safekeeping; it contains secrets."
}
case "$action" in
  1|start) start_app ;;
  2|update)
    echo 'A backup will be made before updating.'
    make_backup
    if [ -d .git ]; then
      git diff --quiet && git diff --cached --quiet || fail 'Local source changes found. Save or commit them before updating.'
      git pull --ff-only || fail 'Could not update source. Check your network and Git branch.'
    else
      echo 'ZIP installation: extract the new release over this folder first, keeping .env, data, media and backups. This action rebuilds the source currently in the folder.'
    fi
    start_app ;;
  3|backup) make_backup ;;
  4|restore)
    mkdir -p backups
    echo 'Restore requires empty destination data and video folders. Existing files will not be overwritten.'
    echo 'Put your .tar.gz archive in the backups folder.'
    read -r -p 'Backup filename: ' archive
    [ -f "backups/$archive" ] && [[ "$archive" != */* ]] || fail 'Choose a filename in backups.'
    read -r -p 'Stop BetterTube and restore this archive? [y/N]: ' confirm
    case "$confirm" in y|Y|yes|YES) ;; *) exit 0 ;; esac
    docker compose stop
    docker compose build bt-api || fail 'Could not build the restore helper. Your files have not been restored.'
    helper -v "$PWD/backups:/backup" bt-api - restore "$archive" || fail 'Restore failed; services remain stopped. Read the error above before starting again.'
    echo 'Restore finished. Run setup.sh to start BetterTube.' ;;
  5|status) docker compose ps ;;
  6|stop) docker compose stop ;;
  7|reset-pin)
    helper bt-api - list-profiles
    read -r -p 'Profile ID to reset the PIN for: ' pid
    [[ "$pid" =~ ^[0-9]+$ ]] || fail 'Enter the profile ID shown above.'
    echo 'When child profiles are enabled, adult PINs are replaced with a new PIN shown here.'
    read -r -p 'Reset this profile PIN? [y/N]: ' confirm
    case "$confirm" in y|Y|yes|YES) helper bt-api - reset-pin "$pid" ;; esac ;;
  *) fail 'Choose start, update, backup, restore, status, stop or reset-pin.' ;;
esac
