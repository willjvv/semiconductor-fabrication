.PHONY: up down reset logs ps verify-m2

up:
	docker compose up --build

down:
	docker compose down

reset:
	docker compose down -v
	docker compose up --build

logs:
	docker compose logs -f

ps:
	docker compose ps

verify-m2:
	docker compose exec gateway python verify_m2.py
