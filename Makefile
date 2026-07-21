.PHONY: install build-images dev stop status ssl-cert

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	@chmod +x scripts/bootstrap-env.sh
	@bash scripts/bootstrap-env.sh
	@$(MAKE) build-images
	@$(MAKE) dev
	@echo ""
	@echo "=== Вход в панель Hoplyra ==="
	@echo "  Логин:    admin"
	@echo "  Пароль:   admin"
	@echo "  Смените пароль в разделе «Настройки» после первого входа."
	@echo ""

ssl-cert:
	@chmod +x scripts/generate-ssl.sh
	./scripts/generate-ssl.sh


build-images:
	@command -v podman >/dev/null 2>&1 || command -v docker >/dev/null 2>&1 || \
	  (echo "==> podman/docker недоступен — образы будут собираться на VPS при деплое"; exit 0)
	bash hoplyra/images/build-all.sh

dev:
	@chmod +x setup-systemd.sh
	./setup-systemd.sh start

stop:
	@chmod +x setup-systemd.sh
	./setup-systemd.sh stop

status:
	@chmod +x setup-systemd.sh
	./setup-systemd.sh status
