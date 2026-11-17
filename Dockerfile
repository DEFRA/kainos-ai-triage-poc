FROM python:3.12-slim

# Ensure the cloud-deployed container has writable temporary storage and enough memory and disk for the documents and rendered pages
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project \
  --trusted-host pypi.org \
  --trusted-host pypi.python.org \
  --trusted-host files.pythonhosted.org

COPY data/ ./data/
COPY environment/ ./environment/
COPY frontend/ ./frontend/
COPY backend/ ./backend/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app

EXPOSE 8501

CMD ["streamlit", "run", "frontend/app/0_Home.py", "--server.address=0.0.0.0", "--server.port=8501"]