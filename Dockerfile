FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev || uv sync --no-dev
RUN uv build --wheel

FROM python:3.12-slim-bookworm
WORKDIR /app
RUN useradd -r -u 1000 -m wger
COPY --from=build /app/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
USER wger
EXPOSE 8765
ENV PYTHONUNBUFFERED=1
CMD ["wger-mcp"]
