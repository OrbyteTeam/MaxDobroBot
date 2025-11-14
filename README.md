# MaxDobroBot
Телеграм-бот, который помогает подбирать волонтёрские активности. Перед запуском:
1. **Заполните конфиги** `cfg.json` и `cfg_parser.json` (там хранятся токены и настройки моделей).
2. Установите зависимости из `requirements.txt`, если запускаете вне Docker.
## Локальный запуск без Docker
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot_main.py
```
## Docker
### Сборка образа
```bash
docker build -t maxdobrobot:latest .
```
### Запуск контейнера
```bash
docker run --rm -it \
  --name maxdobrobot \
  maxdobrobot:latest
```
* `cfg.json` / `cfg_parser.json` монтируются в режиме `read-only`, чтобы секреты не попадали в образ и не перезаписывались.
* `fsm_data.json` подключается в режиме `read-write`, чтобы состояние пользователей сохранялось между перезапусками.