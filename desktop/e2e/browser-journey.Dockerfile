# The browser journey (ADR 0162) on Linux with a really shown window, under Xvfb: the one place where a raced press
# can land inside a frame of another site's process, which a never-shown window cannot route. Test-only.
#
# Build from the committed tree, so the image holds exactly the head under test and nothing of the host:
#   git archive --format=tar HEAD | docker build -f desktop/e2e/browser-journey.Dockerfile -t hz-browser-journey -
# Run, with no network at all (the fixture site is on loopback):
#   docker run --rm --network none --cap-add=SYS_ADMIN --shm-size=1g hz-browser-journey
# SYS_ADMIN is what Chromium's own sandbox helper needs inside a container; the app runs as an ordinary user.
FROM python:3.12-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb xauth openssl ca-certificates fonts-liberation \
      libasound2 libatk-bridge2.0-0 libatk1.0-0 libcairo2 libcups2 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0 \
      libgtk-3-0 libnspr4 libnss3 libpango-1.0-0 libx11-xcb1 libxcomposite1 libxdamage1 libxfixes3 \
      libxkbcommon0 libxrandr2 libxshmfence1 libxss1 libxtst6 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.13.5 \
    && useradd --create-home journey

COPY --chown=journey:journey . /work
WORKDIR /work
USER journey
ENV UV_LINK_MODE=copy
RUN uv sync --frozen \
    && uv run --script scripts/web_build.py build \
    && uv run --script scripts/web_build.py pnpm --dir ../desktop install --frozen-lockfile \
    && uv run --script scripts/web_build.py pnpm --dir ../desktop exec node node_modules/electron/install.js

# Chromium's sandbox helper must be root-owned and setuid, as desktop/ci/restore_linux_sandbox.py sets it in CI.
USER root
RUN chown root:root desktop/node_modules/electron/dist/chrome-sandbox \
    && chmod 4755 desktop/node_modules/electron/dist/chrome-sandbox
USER journey

ENV HZ_JOURNEY_VISIBLE=1 HZ_JOURNEY_PYTHON=/work/.venv/bin/python
CMD ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1440x900x24", \
     "uv", "run", "--script", "scripts/web_build.py", "pnpm", "--dir", "../desktop", "test:browser-journey"]
