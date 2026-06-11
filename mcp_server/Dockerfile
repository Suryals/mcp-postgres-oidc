FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency manifest
COPY pyproject.toml .

# Install all dependencies into the system Python (no lockfile needed)
RUN uv pip install --system \
    "fastmcp>=2.3.0" \
    "asyncpg>=0.29.0" \
    "PyJWT[crypto]>=2.9.0" \
    "httpx>=0.27.0" \
    "pydantic-settings>=2.3.0" \
    "uvicorn[standard]>=0.30.0" \
    "sqlparse>=0.5.0"

# Copy source
COPY . .

EXPOSE 8000

CMD ["python", "server.py"]
