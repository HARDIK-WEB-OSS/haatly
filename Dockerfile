FROM python:3.12-slim-bookworm AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install uv==0.13.9
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 haatly \
    && useradd --system --uid 10001 --gid haatly --no-create-home --shell /usr/sbin/nologin haatly
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY pyproject.toml ./
COPY migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "--factory", "haatly.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
