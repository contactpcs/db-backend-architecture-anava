# Multi-stage build — slim runtime image, no build toolchain (Architecture Section 20)
FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml .
COPY app ./app
RUN pip install --no-cache-dir --target=/build/deps .

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /build/deps /usr/local/lib/python3.12/site-packages
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY certs ./certs
# Only the bootstrap script, not scripts/ as a whole: that folder also holds the dev seeders
# and wipe_all_accounts.py, which have no place in a production image.
COPY scripts/bootstrap_superadmin_cognito.py ./scripts/bootstrap_superadmin_cognito.py

# Run as an unprivileged user — a code-execution bug in the app must not hand
# out root inside the container.
# .local_storage: the local-disk file store (app/integrations/s3.py) is the
# only path the app writes to, so it is the only one the user owns.
RUN useradd --system --no-create-home --uid 10001 anava \
    && mkdir -p /app/.local_storage && chown anava /app/.local_storage
USER anava

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
