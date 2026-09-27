.PHONY: up down reset logs ps

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
