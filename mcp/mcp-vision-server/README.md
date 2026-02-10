# Vision MCP

Локальный MCP-сервер: изображение + текстовый вопрос → ответ
`Qwen/Qwen35-122B-A10B-FP8` через `http://localhost:8080/v1/chat/completions`.
Транспорт MCP — stdio. OpenCode сам запускает и останавливает процесс.

## Установка

Нужен Python 3.11+; на этой машине используйте `python3.12`
(системный `python3` имеет версию 3.6).

```bash
cd /home/appuser/mcp/mcp-vision-server
python3.12 -m venv venv
venv/bin/pip install --cert /etc/ssl/certs/ca-certificates.crt -r requirements.txt
```

`requirements.lock.txt` фиксирует также транзитивные зависимости проверенного окружения.
Для его воспроизведения замените `requirements.txt` в команде установки на `requirements.lock.txt`.
Системный CA bundle нужен для доверия локальной корпоративной инфраструктуре.

## Подключение к OpenCode

Добавьте `vision` в существующий объект `mcp` в
`/home/appuser/.config/opencode/opencode.jsonc`:

```json
"vision": {
  "type": "local",
  "command": [
    "/home/appuser/mcp/mcp-vision-server/venv/bin/python",
    "/home/appuser/mcp/mcp-vision-server/server.py"
  ],
  "cwd": "/home/appuser/mcp/mcp-vision-server",
  "environment": {
    "VISION_BASE_URL": "http://localhost:8080/v1",
    "VISION_MODEL": "Qwen/Qwen35-122B-A10B-FP8",
    "VISION_TIMEOUT_SECONDS": "6000",
    "VISION_ALLOWED_ROOTS": "/home/appuser"
  },
  "enabled": true,
  "timeout": 6060000
}
```

Таймаут модели — **6000 секунд (100 минут)**. Таймаут MCP в OpenCode задаётся
в миллисекундах: **6060000**. Это верхняя граница ожидания, а не задержка перед ответом.
API или промежуточный прокси могут закончить запрос раньше собственным таймаутом.
Изменения конфигурации подхватываются при следующем запуске OpenCode.

Пример запроса в OpenCode:

> Используй vision/analyze_image для /home/appuser/Pictures/photo.png.
> Перепиши весь видимый текст дословно, без пояснений.

## Инструмент analyze_image

```json
{
  "prompt": "Что изображено на фотографии? Ответь по-русски.",
  "image_path": "/home/appuser/Pictures/photo.png"
}
```

Обязательны непустой `prompt` и ровно один источник изображения:

- `image_path`: абсолютный путь на машине сервера (рекомендуется).
- `image_base64`: чистый base64 или `data:image/png;base64,...`.

Необязательный `mime_type` проверяется по реальному формату файла.
Поддерживаются статичные PNG, JPEG, WebP. Предел по умолчанию — 20 MiB и
40 миллионов пикселей; изображение передаётся без изменения размера и качества.
Файлы проверяются с учётом назначения символических ссылок.

Изображение, прикреплённое к чату, не становится аргументом MCP автоматически.
Передавайте доступный серверу файл или готовые байты из клиента. Не просите
основную модель придумывать или переписывать длинный base64.
URL внешнего изображения не принимается; data URL принимается в `image_base64`.

Результат возвращается как структурированный MCP-ответ и совместимый текстовый JSON:

```json
{
  "answer": "На фотографии ...",
  "model": "Qwen/Qwen35-122B-A10B-FP8",
  "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
  "finish_reason": "stop",
  "truncated": false
}
```

`usage` может быть `null`, если API не вернул статистику. `truncated: true`
означает, что модель достигла лимита генерации. Ошибки входа, HTTP и таймауты
возвращаются как ошибки инструмента (`isError: true`), а не ответы модели.

## Настройки

Настройки читаются из окружения; `.env.example` — образец, `.env` автоматически
не загружается. Для OpenCode используйте блок `environment`.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `VISION_BASE_URL` | `http://localhost:8080/v1` | API с суффиксом `/v1` |
| `VISION_MODEL` | `Qwen/Qwen35-122B-A10B-FP8` | Точное имя модели в шлюзе |
| `VISION_TIMEOUT_SECONDS` | `6000` | Полный бюджет HTTP-вызова |
| `VISION_API_KEY` | пусто | Необязательный Bearer-ключ |
| `VISION_ALLOWED_ROOTS` | домашний каталог | Разрешённые каталоги через `:` на Linux |
| `VISION_MAX_IMAGE_MB` | `20` | Максимальный размер файла в MiB |
| `VISION_MAX_IMAGE_PIXELS` | `40000000` | Максимальное число пикселей |
| `VISION_MAX_TOKENS` | `4096` | Лимит генерируемых токенов |
| `VISION_ENABLE_THINKING` | `false` | `chat_template_kwargs.enable_thinking` для Qwen |

Для длинной транскрипции можно увеличить `VISION_MAX_TOKENS`. При включённом
thinking лимита должно хватать и на рассуждения, и на ответ; наружу возвращается
только `message.content`.

HTTP-клиент обращается к API напрямую, без прокси из окружения и без автоматических
повторов запроса. TLS проверяется. Отмена MCP-вызова отменяет ожидание HTTP;
прекращение генерации на стороне API зависит от самого сервиса.
Изображения и вопросы сервер не сохраняет; ключи, base64 и тела HTTP-ошибок
не выводит в логи. Служебные логи идут в stderr, stdout занят MCP-протоколом.
Тестовые артефакты создаются только внутри `data/` проекта.

Клиент отправляет `Accept-Encoding: identity`: локальный шлюз не сохраняет
`Content-Encoding` при пересылке ответа, поэтому запрашивается несжатый JSON.

## Проверки

```bash
venv/bin/python -m unittest -v
venv/bin/python smoke_test.py
venv/bin/python smoke_test.py --live
```

Первая команда проверяет обработку изображений, границы файлового доступа,
HTTP-ошибки, формат ответа, таймаут и отмену. Вторая запускает настоящий MCP-процесс
и проверяет handshake, схему инструмента и ошибки. Третья дополнительно создаёт
`data/smoke.png`, отправляет его в модель через MCP и проверяет распознанное число.
Для создания тестовой картинки нужен шрифт `DejaVuSans.ttf`.

Протокол передачи изображения соответствует
[документации vLLM](https://docs.vllm.ai/en/latest/features/multimodal_inputs/).
