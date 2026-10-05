#!/bin/sh
# Fan guard: hands Dell fans back to the iDRAC when Fan Control is not running.
#
# Fan Control hands the fans back itself on `docker stop`, on errors and when a control loop
# stalls. What it cannot cover is its own sudden death (kill -9, out of memory, Docker gone): the
# iDRAC then keeps the last manual speed with nobody watching the temperature. This script covers
# that from the host. Run it from cron every minute:
#
#   * * * * * /root/docker/fan-guard.sh >> /var/log/fan-guard.log 2>&1
#
# It needs ipmitool on the host. Servers come from the .env file next to it (the same one the
# compose file uses):
#   GUARD_SERVERS="R420 R720"            names of the servers below
#   R420_HOST=10.10.1.120  R420_USERNAME=root  R420_PASSWORD=...
# or, for a single server, the IDRAC_HOST / IDRAC_USERNAME / IDRAC_PASSWORD variables.
# Only Dell iDRAC is supported: the only BMC whose manual mode outlives the controller.

CONTAINER=${CONTAINER:-idrac_fan_web}
ENV_FILE=${ENV_FILE:-$(dirname "$0")/.env}
STATE=${STATE:-/tmp/fan-guard.failures}

case $ENV_FILE in */*) ;; *) ENV_FILE=./$ENV_FILE ;; esac  # `.` searches PATH for a bare name
[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }

status=$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$CONTAINER" 2>/dev/null)
case "$status" in
  "running healthy"|"running starting"|"running ")
    rm -f "$STATE"
    exit 0 ;;
esac

# one bad minute can be a restart in progress; act from the second one, and keep acting while it lasts
failures=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$failures" > "$STATE"
[ "$failures" -lt 2 ] && exit 0

auto() {  # host user password
  [ -n "$1" ] || return 0
  if IPMI_PASSWORD="$3" ipmitool -I lanplus -H "$1" -U "$2" -E raw 0x30 0x30 0x01 0x01 >/dev/null 2>&1; then
    echo "$(date '+%F %T') fan-guard: $CONTAINER is '${status:-missing}', fans of $1 handed back to the iDRAC"
  else
    echo "$(date '+%F %T') fan-guard: $CONTAINER is '${status:-missing}', and $1 refused the command" >&2
  fi
}

if [ -n "$GUARD_SERVERS" ]; then
  for name in $GUARD_SERVERS; do
    eval "auto \"\${${name}_HOST}\" \"\${${name}_USERNAME:-root}\" \"\${${name}_PASSWORD}\""
  done
else
  auto "$IDRAC_HOST" "${IDRAC_USERNAME:-root}" "$IDRAC_PASSWORD"
fi
