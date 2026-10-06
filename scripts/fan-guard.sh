#!/bin/sh
# Fan guard: hands the fans back to the BMC when Fan Control is not running.
#
# Fan Control hands the fans back itself on `docker stop`, on errors and when a control loop
# stalls. What it cannot cover is its own sudden death (kill -9, out of memory, Docker gone): the
# BMC then keeps the last manual speed with nobody watching the temperature. This script covers
# that from the host. Run it from root's cron every minute:
#
#   * * * * * /root/docker/fan-guard.sh >> /var/log/fan-guard.log 2>&1
#
# It needs ipmitool on the host (and sshpass for HPE iLO 4 unlocked). Servers come from the .env
# file next to it, the one the compose file uses:
#   GUARD_SERVERS="R420 X11"                 names of the servers below
#   R420_HOST=10.10.1.120  R420_USERNAME=root  R420_PASSWORD=...  R420_DRIVER=dell
#   X11_HOST=10.10.1.121   X11_USERNAME=ADMIN  X11_PASSWORD=...   X11_DRIVER=supermicro
# DRIVER is dell (default), supermicro or ilo4-unlocked. For a single Dell server the IDRAC_HOST /
# IDRAC_USERNAME / IDRAC_PASSWORD variables are enough.
#
# The container is found by its image (rack-fan-control); set CONTAINER to name it yourself.
# A container that does not exist is left alone: `docker compose down` stops it cleanly, and Fan
# Control hands the fans back on the way out.

ENV_FILE=${ENV_FILE:-$(dirname "$0")/.env}
if [ -w /run ]; then STATE=${STATE:-/run/fan-guard.failures}; else STATE=${STATE:-/tmp/fan-guard.failures.$(id -u)}; fi

case $ENV_FILE in */*) ;; *) ENV_FILE=./$ENV_FILE ;; esac  # `.` searches PATH for a bare name
if [ -f "$ENV_FILE" ]; then
  # the file is run as shell code by root: refuse one that anyone else can change
  if [ -n "$(find "$ENV_FILE" -perm -0022 2>/dev/null)" ]; then
    echo "$(date '+%F %T') fan-guard: $ENV_FILE is writable by others; fix with chmod 600 $ENV_FILE" >&2
    exit 1
  fi
  set -a
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  set +a
fi

log() { echo "$(date '+%F %T') fan-guard: $*"; }

if ! docker info >/dev/null 2>&1; then
  status="docker-down"  # no Docker, no Fan Control: the fans are on their own
else
  if [ -z "$CONTAINER" ]; then
    CONTAINER=$(docker ps -a --format '{{.Names}} {{.Image}}' | awk '$2 ~ /rack-fan-control|idrac-fan/ {print $1; exit}')
    [ -n "$CONTAINER" ] || { rm -f "$STATE"; exit 0; }  # nothing deployed: nothing to guard
  fi
  status=$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$CONTAINER" 2>/dev/null)
  [ -n "$status" ] || { rm -f "$STATE"; exit 0; }       # removed on purpose
fi

case "$status" in
  "running healthy"|"running starting"|"running ")
    rm -f "$STATE"
    exit 0 ;;
esac

# one bad minute can be a restart in progress; act from the second one, and keep acting while it lasts
failures=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$failures" > "$STATE"
[ "$failures" -lt 2 ] && exit 0

send_auto() {  # host user password driver: the BMC's own fan control back, by the way each vendor takes it
  case "${4:-dell}" in
    dell)       IPMI_PASSWORD="$3" ipmitool -I lanplus -H "$1" -U "$2" -E raw 0x30 0x30 0x01 0x01 >/dev/null 2>&1 ;;
    supermicro) IPMI_PASSWORD="$3" ipmitool -I lanplus -H "$1" -U "$2" -E raw 0x30 0x45 0x01 "${SUPERMICRO_MODE:-0x02}" >/dev/null 2>&1 ;;
    ilo4-unlocked)
      ok=1
      for n in 0 1 2 3 4 5 6 7; do
        SSHPASS="$3" sshpass -e ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new           -o KexAlgorithms=+diffie-hellman-group14-sha1,diffie-hellman-group1-sha1 -o HostKeyAlgorithms=+ssh-rsa           -l "$2" "$1" "fan p $n max 255" >/dev/null 2>&1 && ok=0
      done
      return $ok ;;
    *) log "unknown driver '$4' for $1"; return 1 ;;
  esac
}

hand_back() {  # host user password driver
  [ -n "$1" ] || return 0
  if send_auto "$@"; then
    log "container ${CONTAINER:-?} is '$status', fans of $1 handed back to the BMC"
  else
    log "container ${CONTAINER:-?} is '$status', and $1 refused the command" >&2
  fi
}

if [ -n "$GUARD_SERVERS" ]; then
  for name in $GUARD_SERVERS; do
    case $name in ''|*[!A-Za-z0-9_]*) log "skipping invalid server name '$name'"; continue ;; esac
    eval "hand_back \"\${${name}_HOST}\" \"\${${name}_USERNAME:-root}\" \"\${${name}_PASSWORD}\" \"\${${name}_DRIVER:-dell}\""
  done
else
  hand_back "$IDRAC_HOST" "${IDRAC_USERNAME:-root}" "$IDRAC_PASSWORD" dell
fi
