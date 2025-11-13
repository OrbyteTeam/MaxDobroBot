# uploader.py
import os
import uuid
import logging
from pathlib import Path
from aiohttp import web
from urllib.parse import parse_qs

# Путь для сохранения файлов (настройте по желанию)
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# HTML-страница mini-app
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Загрузка файла</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 20px; }
    input[type="file"] { width: 100%; margin: 10px 0; }
    button { width: 100%; padding: 10px; font-size: 16px; }
  </style>
</head>
<body>
  <h2>Загрузите файл</h2>
  <form id="uploadForm" enctype="multipart/form-data">
    <input type="hidden" name="user_id" id="user_id" value="">
    <input type="file" name="file" required>
    <button type="submit">Отправить</button>
  </form>
  <div id="status"></div>

  <script>
    // Получаем user_id из URL-параметров
    const urlParams = new URLSearchParams(window.location.search);
    const userId = urlParams.get('user_id');
    if (!userId) {
      document.body.innerHTML = '<h2>Ошибка: не указан user_id</h2>';
    } else {
      document.getElementById('user_id').value = userId;
    }

    document.getElementById('uploadForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const formData = new FormData(e.target);
      const status = document.getElementById('status');
      status.textContent = 'Загрузка...';

      try {
        const resp = await fetch('/upload', {
          method: 'POST',
          body: formData
        });
        const result = await resp.json();
        if (resp.ok) {
          status.innerHTML = '<span style="color:green">✅ Файл успешно загружен!</span>';
        } else {
          throw new Error(result.error || 'Ошибка сервера');
        }
      } catch (err) {
        status.innerHTML = `<span style="color:red">❌ ${err.message}</span>`;
      }
    });
  </script>
</body>
</html>
'''

routes = web.RouteTableDef()

@routes.get('/')
async def handle_index(request):
    user_id = request.query.get('user_id')
    if not user_id:
        return web.Response(text="Не указан user_id", status=400)
    return web.Response(text=HTML_TEMPLATE, content_type='text/html')

@routes.post('/upload')
async def handle_upload(request):
    try:
        reader = await request.multipart()
        user_id = None
        file_field = None

        async for part in reader:
            if part.name == 'user_id':
                user_id = (await part.read()).decode('utf-8').strip()
            elif part.name == 'file':
                file_field = part

        if not user_id or not file_field:
            return web.json_response({"error": "Отсутствует user_id или файл"}, status=400)

        # Генерируем имя: user_id_num
        num = str(uuid.uuid4().hex[:8])
        filename = f"{user_id}_{num}"
        filepath = UPLOAD_DIR / filename

        # Сохраняем файл
        with open(filepath, 'wb') as f:
            while True:
                chunk = await file_field.read_chunk()
                if not chunk:
                    break
                f.write(chunk)

        logging.info(f"Файл сохранён: {filepath}")
        return web.json_response({"status": "ok", "filename": filename})

    except Exception as e:
        logging.exception("Ошибка при загрузке")
        return web.json_response({"error": str(e)}, status=500)

def create_app():
    app = web.Application()
    app.add_routes(routes)
    return app

# Запуск сервера (для отдельного процесса или совместно с ботом через asyncio)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(), host="192.168.1.137", port=8080)