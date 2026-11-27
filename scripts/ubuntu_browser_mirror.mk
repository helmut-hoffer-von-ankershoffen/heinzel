.PHONY: docs-browser-mirror
docs-browser-mirror:
	sudo python3 -I scripts/ubuntu_browser_mirror.py
	sudo install -o root -g root -m 0644 scripts/ubuntu_browser_mirror.conf /etc/apt/apt.conf.d/99-heinzel-browser-sources
