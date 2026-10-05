# pinned by digest for reproducible builds; Dependabot proposes the updates
FROM python:3.13-alpine@sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a

# ipmitool: Dell, Supermicro and generic IPMI. openssh-client + sshpass: HPE iLO 4 with unlocked firmware.
# tzdata: quiet hours follow the TZ variable.
RUN apk add --no-cache ipmitool openssh-client sshpass tzdata \
 && adduser -D -H -u 1000 app \
 && mkdir /data && chown app:app /data

WORKDIR /app
COPY app.py ./
COPY fanctl ./fanctl
COPY web ./web

ARG VERSION=dev
ENV APP_VERSION=$VERSION DATA_DIR=/data PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
VOLUME /data
EXPOSE 8080
# liveness, not BMC health: a BMC that stops answering must not get the container restarted
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD wget -qO- "http://127.0.0.1:${PORT:-8080}/livez" || exit 1
# exec form: python is PID 1 and receives SIGTERM, so fans go back to automatic on `docker stop`
CMD ["python", "app.py"]
