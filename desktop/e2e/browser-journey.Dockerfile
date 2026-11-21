# The browser journey (ADR 0162) on Linux with a really shown window, under Xvfb: the one place where a raced press
# can land inside a frame of another site's process, which a never-shown window cannot route. Test-only.
#
# Build from the committed tree, so the image holds exactly the head under test and nothing of the host. Commit first:
# after a rebase or an edit, what is not committed is not in the image.
#   git archive --format=tar HEAD | docker build -f desktop/e2e/browser-journey.Dockerfile -t hz-browser-journey -
# Run, with no network at all (the fixture site is on loopback):
#   docker run --rm --network none --cap-add=SYS_ADMIN --shm-size=1g hz-browser-journey
# SYS_ADMIN is what Chromium's own sandbox helper needs inside a container; the app runs as an ordinary user.
FROM python@sha256:4209fae09a3e20afd863e528bfddc235da5fd5977983e1996cd54cc9a72e6267

RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb openssl ca-certificates fonts-liberation \
      libasound2 libatk-bridge2.0-0 libatk1.0-0 libcairo2 libcups2 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0 \
      libgtk-3-0 libnspr4 libnss3 libpango-1.0-0 libx11-xcb1 libxcomposite1 libxdamage1 libxfixes3 \
      libxkbcommon0 libxrandr2 libxshmfence1 libxss1 libxtst6 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --only-binary=:all: uv==0.13.5 \
    && useradd --create-home journey

COPY --chown=root:root pyproject.toml uv.lock .python-version hatch_build.py \
    CHANGELOG.md PYPI.md LICENSE ATTRIBUTIONS.md /work/
COPY --chown=journey:journey src/ /work/src/
COPY --chown=journey:journey agent-skills/skills/ /work/agent-skills/skills/
COPY --chown=root:root scripts/web_build.py scripts/tools.py scripts/tools.toml /work/scripts/
COPY --chown=journey:journey web/package.json web/pnpm-lock.yaml web/index.html web/vite.config.ts web/tsconfig.json /work/web/
COPY --chown=journey:journey web/src/ /work/web/src/
COPY --chown=journey:journey web/public/ /work/web/public/
COPY --chown=journey:journey desktop/package.json desktop/pnpm-lock.yaml /work/desktop/
COPY --chown=journey:journey desktop/src/ /work/desktop/src/
COPY --chown=journey:journey desktop/assets/ /work/desktop/assets/
COPY --chown=journey:journey desktop/e2e/ /work/desktop/e2e/
WORKDIR /work
# Build outputs are writable; the build inputs and helper scripts remain root-owned and cannot be replaced.
RUN mkdir .venv .tools && chown journey:journey .venv .tools
USER journey
ENV UV_LINK_MODE=copy
# The editable wheel is built from this checkout with the backend already installed from uv.lock.
RUN uv sync --frozen --no-build --no-dev --group build --no-install-project \
    && wheel_dir="$(mktemp -d)" \
    && .venv/bin/python -c \
      'from hatchling.build import build_editable; import sys; build_editable(sys.argv[1])' "$wheel_dir" \
    && uv pip install --offline --no-build --no-deps "$wheel_dir"/heinzel-*.whl \
    && rm -r "$wheel_dir" \
    && .venv/bin/python scripts/web_build.py build \
    && .venv/bin/python scripts/web_build.py pnpm --dir ../desktop install --frozen-lockfile \
    && .venv/bin/python scripts/web_build.py pnpm --dir ../desktop exec node node_modules/electron/install.js

# Chromium's sandbox helper must be root-owned and setuid, as desktop/ci/restore_linux_sandbox.py sets it in CI.
USER root
RUN chown root:root desktop/node_modules/electron/dist/chrome-sandbox \
    && chmod 4755 desktop/node_modules/electron/dist/chrome-sandbox
USER journey

# The virtual display is started here and not by xvfb-run: as the container's first process xvfb-run waits forever
# for the display's ready signal. UV_OFFLINE: the run has no network, and everything was fetched by the build.
ENV HZ_JOURNEY_VISIBLE=1 HZ_JOURNEY_PYTHON=/work/.venv/bin/python DISPLAY=:99 UV_OFFLINE=1 \
    HZ_JOURNEY_TALLY=/tmp/hz-journey-in-frame
CMD Xvfb :99 -screen 0 1440x900x24 -nolisten tcp & \
    for turn in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do [ -S /tmp/.X11-unix/X99 ] && break; sleep 0.5; done; \
    [ -S /tmp/.X11-unix/X99 ] || { echo "The virtual display did not start."; exit 1; }; \
    .venv/bin/python scripts/web_build.py pnpm --dir ../desktop test:browser-journey; status=$?; \
    echo "Browser journey container: exit $status"; exit $status
