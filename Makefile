.PHONY: install model api up up-gpu down logs check format setup-directml

install:
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .

model:
python scripts/download_model.py

api:
uvicorn label_controllers.api:app --host 0.0.0.0 --port 8000

up:
docker compose up -d --build

up-gpu:
docker compose -f compose.yaml -f compose.gpu.yaml up -d --build

down:
docker compose down

logs:
docker compose logs -f api

check:
ruff check src scripts
python -m compileall -q src scripts

format:
ruff check --fix src