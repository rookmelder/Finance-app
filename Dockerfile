FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FINANCE_DB=/data/finance.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

# run as a non-root user; /data holds the SQLite database (mount a volume here)
RUN useradd --create-home --uid 1000 app && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8080

CMD ["python", "-m", "src.app"]
