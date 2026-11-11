SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.ONESHELL:
.PHONY: pin verify probe

pin:
	mkdir -p "$(RUNNER_TEMP)/h17g-python-output"
	printf 'RUN=%s ATTEMPT=%s TRANSPORT=%s IMAGE=%s VERSION=%s\n' "$(GITHUB_RUN_ID)" "$(GITHUB_RUN_ATTEMPT)" "$(GITHUB_SHA)" "$(ImageOS)" "$(ImageVersion)" > "$(RUNNER_TEMP)/h17g-python-output/bootstrap.log"
	test "$$(git rev-parse HEAD)" = "$(GITHUB_SHA)"
	sha256sum .github/workflows/h17g-python-mirror.yml diagnostics/h17g-python/diagnostic.mk >> "$(RUNNER_TEMP)/h17g-python-output/bootstrap.log"
	printf '%s  %s\n' \
	  '63701651aa24e4fd5ce712f8e2821cad1b3adc0b2706f061a4d9a71aaf533d87' 'diagnostics/h17g-python/probe.sh' \
	  '127250452f10de8f0e7da73415726c59f3e0ddb1390e3964a0510ac2c9b9edda' 'diagnostics/h17g-python/check_download.py' \
	  'e4aad9a4fefc431a67b14ba2638b00caab98a348396d12de0e4df138b13466e9' 'diagnostics/h17g-python/access.sh' \
	  'fde88e643905efde62f23a0a3b56e9acf15467edaf446e043aaebc9524736902' 'diagnostics/h17g-python/check_indexes.py' \
	  'db885b4911fa7adce7c856dad82aa39e56c41c5b6683874c854221110fc303f4' 'diagnostics/h17g-python/sources.py' \
	  '3d15b10d3d9777587ed11fa1170e857c3eba5c92b71874b849caf86c8156a76d' 'diagnostics/h17g-python/smoke.py' | sha256sum -c -
	cp diagnostics/h17g-python/probe.sh "$(RUNNER_TEMP)/h17g-python-probe.sh"
	cp diagnostics/h17g-python/check_download.py "$(RUNNER_TEMP)/h17g-python-check-download.py"
	cp diagnostics/h17g-python/access.sh "$(RUNNER_TEMP)/h17g-python-access.sh"
	cp diagnostics/h17g-python/check_indexes.py "$(RUNNER_TEMP)/h17g-python-check-indexes.py"
	cp diagnostics/h17g-python/sources.py "$(RUNNER_TEMP)/h17g-python-sources.py"
	cp diagnostics/h17g-python/smoke.py "$(RUNNER_TEMP)/h17g-python-smoke.py"
	cp diagnostics/h17g-python/diagnostic.mk "$(RUNNER_TEMP)/h17g-python.mk"

verify:
	test "$$(git rev-parse HEAD)" = 700d2d634062b41c6336ad2774ec53a4b385b25b
	printf '%s  %s\n' \
	  'e69654046a38ffedf715b297e62c000486725720dc8cb2c81651453fecfe2711' '.github/workflows/docs.yml' \
	  '02b35da38b6570bf550e606e3d6d11800238f69d96244b9f8b911acb29f8f18e' 'scripts/site_check.py' \
	  '1716f502d5316f3d650e1f32d9953145a00464d9369a81d2c92ba6568975ecee' 'Makefile' \
	  '3a3e2331fa05ff7441568d6a24ca3cfc9bb10374dcde704952b699173836ed42' 'uv.lock' \
	  '837f04108c53cab21f2841af0a7e165c72f6c1f1ddea0e4aadd9ece2ab634d90' 'web/pnpm-lock.yaml' | sha256sum -c - | tee "$(RUNNER_TEMP)/h17g-python-output/source-pins.log"
	python3 -c 'import tomllib; p=[x for x in tomllib.load(open("uv.lock","rb"))["package"] if x["name"]=="playwright"]; assert len(p)==1 and p[0]["version"]=="1.64.1"; print("PROJECT_LOCK_PLAYWRIGHT=1.64.1")' | tee -a "$(RUNNER_TEMP)/h17g-python-output/source-pins.log"

probe:
	bash "$(RUNNER_TEMP)/h17g-python-probe.sh" 2>&1 | tee "$(RUNNER_TEMP)/h17g-python-output/summary.log"
