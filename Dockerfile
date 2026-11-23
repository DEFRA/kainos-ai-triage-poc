# Note: ensure the cloud-deployed container has writable temporary storage 
    # and enough memory and disk for anything that needs it
FROM python:3.12-slim

##########################################################
# Not needed for the non-live PoC
##########################################################
# Install LibreOffice to convert DOCX, DOC and RTF attachments to PDF
    # Conversion can fail for damaged or password-protected files and may require significant memory and temporary storage
    # Missing fonts can change the layout of converted documents
# Install antiword for text extraction when DOC-to-PDF conversion fails
#RUN apt-get update \
    #&& apt-get install -y --no-install-recommends \
      #antiword \
        #libreoffice-writer \
        #fonts-liberation \
    #&& rm -rf /var/lib/apt/lists/*
##########################################################

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project \
  --trusted-host pypi.org \
  --trusted-host pypi.python.org \
  --trusted-host files.pythonhosted.org

# Not needed for the non-live PoC, so these lines are commented out
# COPY data/ ./data/ 
# COPY environment/ ./environment/
# COPY backend/ ./backend/
COPY frontend/ ./frontend/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app

EXPOSE 8501

CMD ["streamlit", "run", "frontend/app/0_Home.py", "--server.address=0.0.0.0", "--server.port=8501"]