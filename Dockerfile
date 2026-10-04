FROM python:3.13-alpine
RUN apk add --no-cache ipmitool
WORKDIR /app
COPY app.py index.html login.html ./
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
# exec form: python is PID 1 and receives SIGTERM, so fans go back to Dell mode on `docker stop`
CMD ["python", "app.py"]
