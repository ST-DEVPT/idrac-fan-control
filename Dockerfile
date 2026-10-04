FROM python:3.13-alpine

# ipmitool: Dell, Supermicro and generic IPMI. openssh-client + sshpass: HPE iLO 4 with unlocked firmware.
RUN apk add --no-cache ipmitool openssh-client sshpass \
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
HEALTHCHECK --interval=60s --timeout=5s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
# exec form: python is PID 1 and receives SIGTERM, so fans go back to automatic on `docker stop`
CMD ["python", "app.py"]
