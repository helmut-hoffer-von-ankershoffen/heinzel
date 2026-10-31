.DEFAULT_GOAL := help
TOOLING ?= heinzel-plugin-tools @ https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/tools-v0.2.1/heinzel_plugin_tools-0.2.1-py3-none-any.whl\#sha256=eebaf555c0c3b39cb13d28ae5ea146ec459e06d1b96cf8ba4499ffea4247aab7
unexport TOOLING CORE TAG REGISTRY
export HZ_PLUGIN_TOOLING := $(value TOOLING)
export PLUGIN_CORE := $(value CORE)
export PLUGIN_TAG := $(value TAG)
export PLUGIN_REGISTRY := $(value REGISTRY)
MAKEOVERRIDES =
PLUGIN := uv run --quiet --no-project --frozen --no-build --python 3.14 python scripts/plugin_tooling.py --toolkit "$$HZ_PLUGIN_TOOLING" hz-plugin

.PHONY: help setup fix check-fast check test lock preview build acceptance audit release preflight publish
help:
	@echo 'make setup; make check-fast; make check; make preview; make build; make acceptance'
	@echo 'make lock refreshes this plugin dependency lock; CORE=<wheel> selects a candidate SDK.'
	@echo 'make release previews; make publish dispatches the protected release workflow.'
setup fix check-fast check test lock preview build acceptance audit release preflight:
	@$(PLUGIN) $@
publish:
	@$(PLUGIN) publish
