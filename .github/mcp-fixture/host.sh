#!/usr/bin/env bash
# One disposable fixture, with its host listener and stopped container retained until collection.
set -euo pipefail
mkdir -p mcp-fixture-artifacts
container="mcp-fixture-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"
pid=''
context=''
collect() {
  result=$?
  trap - EXIT
  printf '%s\n' "$result" > mcp-fixture-artifacts/host-exit-code.txt
  if docker inspect "$container" >/dev/null 2>&1; then
    docker inspect "$container" > mcp-fixture-artifacts/container-inspect.json || true
    docker logs "$container" > mcp-fixture-artifacts/container.log 2>&1 || true
    if ! docker cp "$container:/artifacts/." mcp-fixture-artifacts/; then
      echo 'container artifacts missing' > mcp-fixture-artifacts/missing-artifacts.txt
    fi
    docker rm -f "$container" >/dev/null || true
  else
    echo 'container was not created' > mcp-fixture-artifacts/missing-container.txt
  fi
  if [[ -n "$pid" ]]; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; fi
  if [[ -n "$context" ]]; then rm -rf -- "$context"; fi
  exit "$result"
}
trap collect EXIT
image="mcp-isolated:${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
printf '%s\n' "$GITHUB_SHA" > mcp-fixture-artifacts/source-sha.txt
source_sha=ff85f84818fca13645789602fd2337af1ca27d49
test "$(git -C mcp-source rev-parse HEAD)" = "$source_sha"
printf '%s\n' "$source_sha" > mcp-fixture-artifacts/product-source-sha.txt
# Build context is the exact tracked product tree plus five reviewed helper files, never a checkout or host mount.
context=$(mktemp -d "${RUNNER_TEMP:?}/mcp-context.XXXXXXXX")
git -C mcp-source archive "$source_sha" | tar -x -C "$context"
mkdir -p "$context/.github/mcp-fixture"
cp .github/mcp-fixture/Dockerfile "$context/.github/mcp-fixture/Dockerfile"
cp scripts/mcp_isolated_fixture.py "$context/scripts/mcp_isolated_fixture.py"
cp .github/mcp-fixture/mcp-settings-py.fixture "$context/tests/e2e/mcp_settings_test.py"
cp .github/mcp-fixture/mcp-settings.mjs "$context/web/e2e/mcp-settings.mjs"
sha256sum "$context/tests/e2e/mcp_settings_test.py" "$context/web/e2e/mcp-settings.mjs" "$context/uv.lock" "$context/web/pnpm-lock.yaml" > mcp-fixture-artifacts/source-hashes.txt
docker build --file "$context/.github/mcp-fixture/Dockerfile" --tag "$image" "$context" > mcp-fixture-artifacts/build.log 2>&1
docker image inspect "$image" --format '{{.Id}}' > mcp-fixture-artifacts/image-id.txt
host=$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')
test -n "$host"
python3 -u -c 'import socket,sys
s=socket.socket(); s.bind((sys.argv[1],0)); s.listen(4); s.settimeout(900)
print(s.getsockname()[1],flush=True)
while True:
 c,_=s.accept()
 with c: c.sendall(b"synthetic-host-control")' "$host" > mcp-fixture-artifacts/host-listener.log 2>&1 &
pid=$!
for _ in {1..50}; do [[ -s mcp-fixture-artifacts/host-listener.log ]] && break; sleep .1; done
port=$(head -n 1 mcp-fixture-artifacts/host-listener.log)
[[ "$port" =~ ^[0-9]+$ && "$port" != 8737 ]]
# Positive control: same image can reach the inert listener when explicitly on the bridge.
docker run --rm --network bridge --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 \
  --env CONTROL_HOST="$host" --env CONTROL_PORT="$port" --entrypoint /opt/hz/.venv/bin/python "$image" \
  -c 'import os,socket; s=socket.create_connection((os.environ["CONTROL_HOST"],int(os.environ["CONTROL_PORT"])),2); assert s.recv(64)==b"synthetic-host-control"; s.close(); print("control passed")' > mcp-fixture-artifacts/control.log 2>&1
# No mounts, capabilities, host network, credential environment or shared IPC/PID.
# Based on Playwright v1.64.1 seccomp profile (commit cfe490d941f992655948eee8fd4fd94e1c4234bf;
# upstream SHA256 cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849).
# The single delta removes CAP_SYS_CHROOT from the chroot rule so Chromium can use it in its
# own user namespace. The container receives no capabilities and the kernel still checks them.
docker create --name "$container" --network none --ipc private --cap-drop ALL \
  --security-opt seccomp=.github/mcp-fixture/seccomp.json --security-opt no-new-privileges --user 10001:10001 --pids-limit 256 --shm-size 1g \
  --env CONTROL_HOST="$host" --env CONTROL_PORT="$port" "$image" > mcp-fixture-artifacts/container-id.txt
docker start -a "$container"
