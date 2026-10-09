# Migrated runtime. The application is mounted read-only at /app at run time: the
# stub API during development, the real C1-C3 output once it exists.
FROM python:3.11-slim

# The application mount is read-only, so Python must not try to write bytecode.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
