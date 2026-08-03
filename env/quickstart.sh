#!/usr/bin/env bash
# Thin wrapper over DataHub's own quickstart. OWNER B (@teammate).
#
# This does not reimplement anything: `datahub docker quickstart` is the
# supported path. The wrapper exists to make it one command, to fail with a
# message that says what to do, and to keep the URLs in one place.
set -euo pipefail

GMS_URL="${DATAHUB_GMS_URL:-http://localhost:8080}"
FRONTEND_URL="${DATAHUB_FRONTEND_URL:-http://localhost:9002}"

usage() {
  cat <<'USAGE'
Usage: ./env/quickstart.sh <command>

  up        Start the local DataHub quickstart
  down      Stop it, keeping data
  status    Show whether GMS is answering
  reset     Stop it and DELETE all local DataHub data (asks first)
  urls      Print the frontend and GMS URLs
USAGE
}

require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: '$1' is not installed or not on PATH." >&2
    echo "       $2" >&2
    exit 1
  }
}

check_prereqs() {
  require docker "Install Docker Desktop: https://docs.docker.com/get-docker/"
  require datahub "Install the DataHub CLI: uv tool install acryl-datahub"
  docker info >/dev/null 2>&1 || {
    echo "error: Docker is installed but not running. Start Docker and retry." >&2
    exit 1
  }
}

case "${1:-}" in
  up)
    check_prereqs
    echo "Starting DataHub quickstart. First run pulls several GB of images."
    datahub docker quickstart
    echo
    echo "  Frontend: ${FRONTEND_URL}   (datahub / datahub)"
    echo "  GMS:      ${GMS_URL}"
    echo
    echo "Next: uv run python env/seed_demo.py"
    ;;
  down)
    check_prereqs
    datahub docker quickstart --stop
    ;;
  status)
    if curl -fsS "${GMS_URL}/health" >/dev/null 2>&1; then
      echo "GMS is answering at ${GMS_URL}"
    else
      echo "GMS is not answering at ${GMS_URL}. Try: ./env/quickstart.sh up" >&2
      exit 1
    fi
    ;;
  reset)
    check_prereqs
    echo "This DELETES all data in your local DataHub quickstart."
    read -r -p "Type 'reset' to confirm: " confirm
    [ "$confirm" = "reset" ] || { echo "Aborted."; exit 1; }
    datahub docker nuke
    ;;
  urls)
    echo "frontend=${FRONTEND_URL}"
    echo "gms=${GMS_URL}"
    ;;
  *)
    usage
    exit 1
    ;;
esac
