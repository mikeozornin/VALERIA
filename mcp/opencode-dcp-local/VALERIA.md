# DCP с исправлением cooldown в Valeria

Версия: `3.2.0-local.1`, исходная копия: `/home/appuser/mcp/opencode-dcp-local`.
Описание исправления и регрессионные тесты: [LOCAL-FIX.md](LOCAL-FIX.md).
Исходники, оригинальный upstream bundle и собранный `dist/` хранятся в Git.
`node_modules/` и логи в Git не входят.

## Проверка

При наличии зависимостей из исходной локальной копии:

```sh
cd mcp/opencode-dcp-local
npm run build:local
npm run test:local
```

`build:local` заменяет только исправленный модуль в опубликованном upstream
bundle. Обычный upstream `build` здесь не применяется: исходная поставка
не содержит полного build setup. `DCP_TEST_UPSTREAM=1 npm run test:local`
воспроизводит ошибки до исправления.

## Сервер

Каталог: `/opt/valeria/mcp/opencode-dcp-local`. Вместе с исходниками и `dist/`
нужны зависимости `node_modules/` из проверенной локальной копии.
Перед изменением конфигов сохранить резервные копии с временной меткой.
В `/root/.config/opencode/opencode.jsonc` заменить только запись DCP:

```json
"plugin": ["file:///opt/valeria/mcp/opencode-dcp-local"]
```

Сохранить остальные плагины, если они есть. В `/root/.config/opencode/dcp.jsonc`
добавить `"autoUpdate": false`, сохранив остальные настройки.
Проверить загрузку через отдельный `opencode serve` на loopback:
`/global/health`, путь плагина в `/config`, инструмент `compress` в
`/experimental/tool/ids` и команду `dcp-compress` в `/command`.
Для применения в существующих workspace нужен перезапуск их OpenCode.
Перезапускать `valeria-web.service` только после проверки отсутствия активных
заданий и занятых сессий. Прокси для смены плагина перезапускать не требуется.

## Откат

Вернуть только запись плагина на `@tarquinen/opencode-dcp@latest` и прежнее
значение `autoUpdate` из резервной копии, затем перезапустить OpenCode при
отсутствии активных заданий. Позднейшие изменения других настроек сохранить.

Тесты cooldown и регистрация плагина не заменяют наблюдения за длинной
реальной модельной сессией. Фикс управляет напоминаниями DCP; модель по-прежнему
может сама вызывать `compress`.
