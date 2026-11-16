# Legacy discovery probe

This is an instrumented diagnostic, not acceptance evidence. It calls the published 0.9.43 updater's original functions and executes its real update, so it can change the installed tool. Wrapping adds overhead, changes the process entry path and redirects the CLI output; it cannot prove an uninstrumented update would have the same outcome. A successful run does not reverse a failed release acceptance.

Use **only a dedicated disposable VM with a separate user and service manager**. A private HOME is insufficient: the published updater can access absolute service-manager commands and shared service identities. Never run this probe on a development machine or a machine with a real Heinzel service. The confirmation flag is an operator assertion, not automatic VM detection. Destroy the VM after any timeout or incomplete probe; terminating the owned process group cannot prove that no descendant escaped it.

Prepare a trusted private case directory containing the retained 0.9.43 tool and its uv receipt at `tools/heinzel`, its managed Python 3.12 at `uv-python`, and the byte-identical published wheel at `bridge.whl`. Preserve the plugins, constraints and configuration of the case being diagnosed. The wheel must have SHA-256 `f18571dae4d3533598eaa645367cbe7432d5f915360f01814faf64352bc93b7a`; the probe checks it, compares installed Core Python source and metadata to the wheel, and refuses extra Core source or cached Core bytecode. Remove only the disposable fixture's Core bytecode before running. The interpreter and third-party packages still require a trusted fixture. Prepare a private `uv` executable under `bin` and ensure any service-manager commands cannot affect another installation. A complete fixture needs its own `home`, `tmp`, `uv-cache`, XDG directories and the Heinzel directories below.

On that VM, set `case_root` to the absolute private case directory and `probe_script` to the absolute path of this script. Run from the case directory so checkout-local configuration cannot affect the update:

```sh
case_root=/absolute/path/to/private-case
probe_script=/absolute/path/to/checkout/scripts/acceptance/legacy_discovery_probe.py
cd "$case_root"
env -i HOME="$case_root/home" TMPDIR="$case_root/tmp" \
  PATH="$case_root/bin:/usr/bin:/bin" UV_NO_CONFIG=1 \
  UV_TOOL_DIR="$case_root/tools" UV_TOOL_BIN_DIR="$case_root/bin" \
  UV_PYTHON_INSTALL_DIR="$case_root/uv-python" UV_CACHE_DIR="$case_root/uv-cache" \
  XDG_CACHE_HOME="$case_root/xdg-cache" XDG_CONFIG_HOME="$case_root/xdg-config" \
  XDG_DATA_HOME="$case_root/xdg-data" XDG_STATE_HOME="$case_root/xdg-state" \
  HZ_CONFIG_HOME="$case_root/config_home" HZ_DATA_DIR="$case_root/data_dir" \
  HZ_CACHE_DIR="$case_root/cache_dir" HZ_STATE_DIR="$case_root/state_dir" \
  HZ_LOG_DIR="$case_root/log_dir" HZ_SYSTEM_DAEMON__UNIT_DIR="$case_root/units" \
  HZ_UPDATE_CHECK=false HZ_SYSTEM_CREDENTIAL__BACKEND=environment \
  "$case_root/tools/heinzel/bin/python" -I -S "$probe_script" \
  --case-root "$case_root" --wheel "$case_root/bridge.whl" --confirm-dedicated-vm
```

The result is bounded JSON tagged `instrumented-non-acceptance`. It records whether the actual `highest_version`, `requires_python`, `_target_release` and `needed_python` calls returned or raised, their durations, safe exception categories and HTTP status where the exception retains one, plus the selected post-update interpreter. Successful-call HTTP status is not observed, and a translated rate-limit exception may not retain the original HTTP status. A supervisor discards worker, child and exit-handler output; only a separate private result pipe returns sanitized facts. `-S` suppresses site startup hooks; the probe restores the private tool prefix and its verified package directory manually, and bypasses the console-entry launcher. These are additional fidelity differences. `events_truncated` and `output_truncated` flag incomplete evidence from the bounded Python capture; native output is discarded. Record the initial receipt and state separately using a privacy-reviewed process; no diagnostic output proves the original failed run's cause.
