.PHONY: install install-api install-ui build-images dev dev-api dev-ui build build-ui ssl-cert

install: install-api install-ui build-images start-service

start-service:
	cd backend && bash setup-systemd.sh start

ssl-cert:
	cd backend && bash scripts/generate-ssl.sh


build-images:
	@command -v podman >/dev/null 2>&1 || command -v docker >/dev/null 2>&1 || \
	  (echo "==> podman/docker не найден — образы будут собираться на VPS при деплое"; exit 0)
	cd backend && bash hoplyra/images/build-all.sh

dev:
	./scripts/dev-local.sh

install-api:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && bash scripts/bootstrap-env.sh

install-ui:
	@if command -v npm >/dev/null 2>&1; then (cd frontend && npm install); else echo "==> npm не найден — используется готовая статика из backend/hoplyra/static"; fi

build-ui:
	@if command -v npm >/dev/null 2>&1; then (cd frontend && npm run build && mkdir -p ../backend/hoplyra/static && cp -r dist/* ../backend/hoplyra/static/); else echo "==> npm не найден — используется готовая статика из backend/hoplyra/static"; fi

build: build-ui
