# Laya System 1 — zero-dependency build.
# engine.py + app.py are pure Python stdlib → NO pip install, NO network
# access needed at build time (works on slow/unstable connections).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY app.py engine.py ./

# run as non-root
RUN useradd -m laya
USER laya

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status < 500 else 1)"

CMD ["python", "app.py"]
