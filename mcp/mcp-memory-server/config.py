"""
Конфигурация MCP сервера памяти.
"""
import os
from pathlib import Path

# Путь к базе данных (по умолчанию в домашней директории)
DEFAULT_DB_PATH = Path.home() / ".mcp-memory" / "memory.db"

# Можно переопределить через переменную окружения
DATABASE_PATH = Path(os.getenv("MCP_MEMORY_DB_PATH", DEFAULT_DB_PATH))

# Убедимся, что директория существует
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

# Лимит по умолчанию для выдачи записей
DEFAULT_LIMIT = 5

# Верхняя граница защищает MCP-клиент от случайной выгрузки всей памяти
MAX_LIMIT = 100

# Защита от чрезмерно больших SQL-запросов с IN (...)
MAX_TAGS_PER_QUERY = 50
