FROM python:3.11-slim-bookworm AS builder

WORKDIR /build
ENV PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN python -m pip install --upgrade pip && \
    pip install --prefix=/install -r requirements.txt


FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --create-home app

COPY --from=builder /install /usr/local
COPY . .
RUN mkdir -p /app/data && chown -R app:app /app

USER app

CMD ["python", "-m", "vibe.bot"]