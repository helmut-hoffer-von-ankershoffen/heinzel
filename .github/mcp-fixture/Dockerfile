# The manifest list digest was resolved from Docker Hub on 2026-11-10.
FROM python:3.14.0-slim-bookworm@sha256:d13fa0424035d290decef3d575cea23d1b7d5952cdf429df8f5542c71e961576
ENV UV_FROZEN=1 PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
WORKDIR /opt/hz
COPY . /opt/hz
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv==0.13.7 \
 && uv run --script scripts/build.py sync \
 && uv run --script scripts/web_build.py build \
 && uv run --script scripts/web_build.py exec playwright-core install --with-deps chromium \
 && useradd -m -u 10001 fixture \
 && mkdir -p /artifacts /home/fixture/config /home/fixture/cache \
 && chown -R 10001:10001 /artifacts /home/fixture \
 && chmod -R a-w /opt/hz /opt/playwright
ENV HOME=/home/fixture TMPDIR=/tmp XDG_CONFIG_HOME=/home/fixture/config XDG_CACHE_HOME=/home/fixture/cache
USER 10001:10001
ENTRYPOINT ["/opt/hz/.venv/bin/python", "/opt/hz/scripts/mcp_isolated_fixture.py"]
