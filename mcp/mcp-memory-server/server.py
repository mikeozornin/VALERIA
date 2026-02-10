"""
MCP сервер памяти для CLine.
Предоставляет инструменты для работы с единой памятью и тегами.
"""
import asyncio

from mcp.server.fastmcp import FastMCP

import models
from database import init_db
from vector_index import lifespan

# Инициализация MCP сервера
mcp = FastMCP("memory", lifespan=lifespan)

# Инициализация базы данных при старте
init_db()


@mcp.tool()
async def get_context(
    query: str | None = None,
    tags: list[str] | None = None,
    match_all_tags: bool = True,
    limit: int = 5,
    include_superseded: bool = False,
) -> list[dict]:
    """Найти контекст по всей памяти по тексту задачи и необязательным тегам.

    query: поиск BM25 + embeddings при включённом гибридном режиме; иначе BM25.
    Без query — последние изменённые заметки. При сбое embeddings — BM25.
    tags: точные имена тегов; None или [] не ограничивают поиск.
    match_all_tags: True — нужны все теги, False — хотя бы один.
    Фильтры применяются до ранжирования и limit (1–100).
    Устаревшие заметки скрыты, если include_superseded=False.
    Результат: заметки с id, content, tags, version, status, временными метками,
    ссылкой superseded_by_note_id и relevance_score (RRF с приоритетом точных идентификаторов в гибридном режиме,
    -BM25 в лексическом, None без query; это не вероятность релевантности).
    """
    return await asyncio.to_thread(
        models.get_context, query, tags, match_all_tags, limit, include_superseded
    )


@mcp.tool()
def get_notes_by_tags(
    tags: list[str],
    match_all_tags: bool = True,
    page: int = 1,
    limit: int = 5,
    include_superseded: bool = False,
) -> dict:
    """Получить заметки единой памяти по точным именам тегов с пагинацией.

    match_all_tags=True требует все теги; False — любой из них.
    Пустой список tags возвращает пустую выборку. page >= 1, limit от 1 до 100.
    Устаревшие заметки скрыты, если include_superseded=False.
    Результат: notes, total, page, total_pages.
    """
    return models.get_notes_by_tags(tags, match_all_tags, page, limit, include_superseded)


@mcp.tool()
def add_note(content: str, tags: list[str]) -> dict:
    """Добавить самостоятельную заметку в единую память.

    content: непустой Markdown с заголовком и областью применения решения.
    tags: список тегов (может быть пустым), например architecture, conan,
    component:example-backend. Одна заметка может относиться к нескольким компонентам.
    Результат: заметка с id, content, tags, version, status и временными метками.
    """
    return models.add_note(content, tags)


@mcp.tool()
def get_all_tags() -> list[dict]:
    """
    Получить список всех тегов.

    Returns:
        list[dict]: Список тегов с полями id, name, created_at
    """
    return models.get_all_tags()


@mcp.tool()
def get_note_by_id(note_id: int) -> dict | None:
    """
    Получить заметку по ID.

    Args:
        note_id: ID заметки

    Returns:
        dict | None: Заметка с полями id, content, version, status,
                     created_at, updated_at, tags
                     или None если не найдена
    """
    return models.get_note_by_id(note_id)


@mcp.tool()
def update_note(
    note_id: int,
    expected_version: int,
    content: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """
    Обновить текст и/или теги заметки с оптимистичной блокировкой.

    Передавай version, полученную из get_note_by_id. Если заметку уже
    изменил другой клиент, вернется status=conflict и current_note;
    данные не будут перезаписаны.
    """
    return models.update_note(note_id, expected_version, content, tags)


@mcp.tool()
def resolve_note_conflict(
    note_id: int,
    expected_current_version: int,
    merged_content: str,
    merged_tags: list[str],
) -> dict:
    """
    Сохранить ручную объединенную версию после update_note/status=conflict.

    expected_current_version должен равняться version из current_note. Повторное
    параллельное изменение также будет отклонено как конфликт.
    """
    return models.resolve_note_conflict(
        note_id,
        expected_current_version,
        merged_content,
        merged_tags,
    )


@mcp.tool()
def get_note_history(note_id: int, limit: int = 20) -> list[dict]:
    """Получить историю версий заметки, от новой к старой."""
    return models.get_note_history(note_id, limit)


@mcp.tool()
def restore_note_version(
    note_id: int,
    version: int,
    expected_version: int,
) -> dict:
    """
    Восстановить текст, теги и статус из прошлой версии.

    Восстановление не стирает историю, а создает новую версию.
    """
    return models.restore_note_version(note_id, version, expected_version)


@mcp.tool()
def mark_note_superseded(
    note_id: int,
    replacement_note_id: int,
    expected_version: int,
) -> dict:
    """
    Пометить решение устаревшим и связать его с активной заменой.

    Замещающая заметка должна быть активной. По умолчанию
    get_context и get_notes_by_tags больше не возвращают устаревшую заметку.
    """
    return models.mark_note_superseded(
        note_id,
        replacement_note_id,
        expected_version,
    )


@mcp.tool()
def delete_note(note_id: int) -> bool:
    """
    Удалить заметку по ID.

    Args:
        note_id: ID заметки для удаления

    Returns:
        bool: True если заметка удалена, False если не найдена
    """
    return models.delete_note(note_id)


if __name__ == "__main__":
    # Запуск MCP сервера
    mcp.run()
