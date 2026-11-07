# Kappacut — web + worker share this image.

# --- frontend build ---------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- python runtime --------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/srv

# The fonts are what captions in any language are drawn with: without them, libass draws
# Chinese, Japanese, Korean, Thai, Hindi and the rest as empty boxes, with no error anywhere.
# DejaVu is named because captions use it first, for the look they've always had, and it
# stops being installed by itself once other fonts are.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl fonts-dejavu-core fonts-noto-core fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY kappacut.py pipeline.py kappacut.toml categories.toml ./
# the colour presets are shared with the editor; one file, read by both
COPY frontend/src/editor/colorPresets.json ./color_presets.json
COPY models ./models
COPY --from=frontend /fe/dist ./web

# Web and worker both run ffmpeg on files strangers upload; a parser bug there should land
# in an unprivileged account, not root. Nothing writes under /srv at runtime - scratch
# space is the temp dir - so the code stays root-owned and read-only to this user.
RUN useradd --system --create-home --uid 10001 kappacut
USER kappacut

EXPOSE 8000

# web
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
