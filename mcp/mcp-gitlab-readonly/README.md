# GitLab MCP (SSH Git + REST API)

Локальный MCP-сервер для полноценного рабочего цикла с GitLab.com или любым
self-managed GitLab. REST endpoint задаётся через `GITLAB_URL`, а SSH endpoint
по умолчанию автоматически берётся из hostname этого URL.

Историческое имя каталога — `mcp-gitlab-readonly`, но сам MCP теперь называется
`gitlab` и поддерживает операции записи.

## Транспорт

- Все сетевые Git-операции (`clone`, `fetch`, `pull`, `push`) выполняются только
  по SSH URL, построенному из проверенного `namespace/project` и разрешённого
  `GITLAB_SSH_HOST`.
- Перед `fetch`, `pull` и `push` MCP проверяет remote. HTTP/HTTPS remote или SSH
  remote на другой хост отклоняется до сетевого вызова.
- GitLab REST API для проектов, merge requests, issues и pipelines продолжает
  работать по HTTPS. Эти операции невозможно выполнить через Git SSH protocol.
- `gitlab_configure_ssh_remote` переводит существующий репозиторий на SSH сразу
  для fetch и push URL.

Таким образом, HTTPS используется только для `/api/v4`; `git fetch` по HTTPS в
этом MCP отсутствует.

## Git-инструменты

- `gitlab_clone_repository` — полный или shallow clone по SSH;
- `gitlab_configure_ssh_remote` — настроить SSH fetch/push URL;
- `gitlab_repository_status`, `gitlab_repository_diff`;
- `gitlab_switch_branch`;
- `gitlab_stage_paths`, `gitlab_unstage_paths`;
- `gitlab_commit`;
- `gitlab_fetch`, `gitlab_pull` (`--ff-only`);
- `gitlab_push` без force;
- `gitlab_commit_and_push` — commit выбранных файлов и push по SSH.

`gitlab_list_pipelines` принимает `sha`, поэтому состояние pipeline можно
получить напрямую для точного commit SHA. Детали доступны через
`gitlab_get_pipeline`, полный downstream-граф — через
`gitlab_get_pipeline_graph`, список jobs — через `gitlab_list_pipeline_jobs`,
trigger jobs — через `gitlab_list_pipeline_bridges`, а лог job — через
`gitlab_get_job_trace`.

`gitlab_get_job_trace` сохраняет полный trace в файл на хосте MCP, а в ответ
возвращает ограниченный preview для модели. Путь к полному `.log` есть в полях
`trace_file` и `full_trace_available_at`.

Пример комбинированной операции:

```text
repository_path=/home/appuser/projects/backend
message=Fix request validation
paths=["src/api.py", "tests/test_api.py"]
remote=origin
branch=feature/validation
```

Если `paths` не передан, commit создаётся из уже подготовленного staging area.
Если `paths` передан, MCP требует, чтобы staging area до операции был пустым.

## GitLab API-инструменты

Сохранены все прежние операции чтения проектов, файлов, commits, merge requests,
issues, pipelines, jobs и traces. Добавлены:

- создание и изменение merge request;
- комментарии к merge request;
- merge с обязательной проверкой ожидаемого head SHA;
- создание и изменение issues;
- комментарии к issues;
- запуск нового pipeline на ветке/теге, retry и cancel pipeline;
- retry отдельной job;
- все правила protected branches, включая wildcard-имена;
- выбранные CI/CD-настройки инсталляции (требуются права администратора).

### Запуск и повтор CI

```text
gitlab_run_pipeline(project="group/project", ref="feature/cache")
gitlab_retry_job(project="group/project", job_id=2001)
```

Первый инструмент возвращает `pipeline_id`, `status`, `ref`, `web_url`, второй —
новые `job_id`, `status`, `web_url`. Retry создаёт новую job: мониторить нужно
возвращённый ID. Токену нужен scope `api` и соответствующие права в проекте.
GitLab применяет правила CI и защиты веток. Запросы записи автоматически не
повторяются; после таймаута проверьте pipelines/jobs перед повторным вызовом,
поскольку GitLab мог уже выполнить операцию.

### Полный CI-граф

Для полного рекурсивного графа используйте один высокоуровневый вызов:

```text
gitlab_get_pipeline_graph(project="group/project", pipeline_id=12345)
```

Инструмент сам проходит все страницы обычных jobs с `include_retried=true` и
trigger jobs каждого найденного pipeline. Рёбра строятся только по
`bridge.downstream_pipeline`; совпадающий SHA или URL не создаёт неявную связь.
Ответ содержит нормализованные `pipelines`, `jobs`, `edges`, а также:

- `coverage.complete` — весь ли доступный граф пройден в заданных пределах;
- `coverage.parameters` — фактически использованные настройки retry и лимитов;
- `coverage.nodes` — полноту jobs/bridges и число страниц для каждого pipeline;
- `coverage.errors` — недоступные pipeline или коллекции без потери уже собранной
  части графа;
- `coverage.limits_hit` — срабатывание `max_depth`, `max_pipelines` или
  `max_pages_per_collection`;
- `snapshot` — менялись ли статусы во время сбора и остались ли активные pipeline.

По умолчанию лимиты равны 20 уровням, 200 pipelines и 100 страницам на каждую
коллекцию. `refresh_statuses=true` повторно читает статусы посещённых pipelines.
Снимок не атомарный: для активного pipeline `coverage.complete=true` означает
полный обход на момент запросов, но не неизменность графа. Метаданные артефактов
сохраняются в jobs, однако трейсы и содержимое артефактов автоматически не
скачиваются.

Низкоуровневые инструменты остаются для точечного чтения и повторной проверки.

### Два режима списка jobs

`gitlab_list_pipeline_jobs(..., compact=false)` сохраняет прежний полный JSON.
С `compact=true` ответ содержит `columns`, `items` (строки) и прежнюю `pagination`:

```text
job_id | status  | duration_seconds | name
18421  | success | 42.1             | "build"
18422  | running | —                | "test"
```

Одна строка на job, без commits/users/runners. Время — секунды, `—` означает
неизвестную длительность, а `0` сохраняется. Имя — JSON-строка с экранированными
переносами. `scope`, `include_retried`, `page`, `per_page` одинаковы в обоих режимах.

Trigger jobs (bridges) не входят в GitLab endpoint обычных jobs. Для полного
графа pipeline отдельно вызывайте
`gitlab_list_pipeline_bridges(project, pipeline_id, compact=true)` и проходите
все страницы. Компактный ответ содержит ID и статус bridge, `allow_failure`, а
также ID, project ID, статус и URL `downstream_pipeline`. Полный режим сохраняет
исходные метаданные GitLab. Если `downstream_pipeline=null`, дочерний pipeline
не был создан или недоступен; это не означает, что обычные jobs pipeline уже
проверены.

### Выборочное чтение trace

```text
gitlab_get_job_trace(project="group/project", job_id=18422, offset=-100, limit=100)
gitlab_get_job_trace(project="group/project", job_id=18422,
                     pattern="ccache --show-stats", context_after=30, limit=100)
```

- `offset` — смещение в строках с нуля; отрицательное — от конца.
- `limit` — положительное максимальное число строк; без него действует только
  общий лимит символов `GITLAB_MAX_TRACE_CHARS`.
- `pattern` — непустая буквальная подстрока, поиск с учётом регистра, без regex.
- `context_before` / `context_after` — неотрицательное число строк вокруг
  совпадения; требуют `pattern`. Пересекающиеся и соседние блоки объединяются.
- При поиске `offset`/`limit` применяются **к объединённой выборке**, после поиска.

Выборка содержит исходные номера строк с единицы (`123: text`). Метаданные
`selection` показывают общее число строк, число совпадений и строк после отбора,
возвращённое число строк, нормализованный `offset` и `next_offset` для продолжения.
`truncated` означает, что часть выбранных строк/символов не попала в ответ;
отброшенные поиском строки сами по себе не считаются усечением.
`character_limit_reached` показывает срабатывание лимита символов. По возможности
возвращаются целые строки; если первая строка длиннее лимита, возвращается её
начало с `last_line_truncated=true`. Полная строка доступна в `trace_file`.
При слишком малом лимите даже для номера строки ответ может быть пустым;
увеличьте `GITLAB_MAX_TRACE_CHARS` или прочитайте сохранённый файл.

Без новых параметров формат и символьный preview остаются прежними. Во всех
режимах полный trace скачивается и сохраняется на хосте MCP: параметры сокращают
ответ модели, а не сетевой трафик к GitLab. `characters` относится к полному логу,
`returned_characters` — к возвращённому тексту, включая номера строк.

### Артефакты и логи установки

`gitlab_get_job_artifact(project, job_id, artifact_path="install_logs.zip")`
скачивает архив job и возвращает список файлов с размерами. Имя архива берётся
из `artifacts_file.filename` или `artifacts` (`file_type=archive`) в метаданных
job; инструмент использует endpoint целого архива `/jobs/:id/artifacts`.
Для отдельного файла внутри архива можно передать его путь в `artifact_path`.

Для чтения скачанного zip укажите `inner_path` из полученного списка файлов,
например `deploy_single_manifest.log` или `installReports/Deploy-Application_*.html`
(нужен точный путь, wildcard не поддерживается). `offset`, `limit`, `pattern`
и контекст работают как у trace; текст возвращается в `content`, сведения об
отборе — в `selection`. Большие логи читайте поиском или окнами.

Каждый вызов заново скачивает артефакт и сохраняет его на хосте MCP в
`gitlab-artifacts/` рядом с каталогом трейсов. Лимит скачивания задаётся
`GITLAB_MAX_ARTIFACT_BYTES` (по умолчанию 100 МиБ). Учитывайте
`artifacts_expire_at`: после удаления артефакта GitLab вернёт ошибку.
Для корпоративного GitLab нужен `GITLAB_CA_BUNDLE`, как и для остальных API-вызовов.

Если GitLab перенаправляет скачивание в object storage, точные HTTPS-хосты
нужно перечислить через запятую в `GITLAB_ARTIFACT_REDIRECT_HOSTS`. MCP допускает
не более трёх проверенных редиректов, никогда не передаёт `PRIVATE-TOKEN` после
первого запроса к GitLab и сохраняет прежние лимиты размера и времени. HTTP,
URL со встроенными учётными данными, нестандартные порты и неразрешённые хосты
отклоняются. Поле `artifact_redirects_followed` показывает число переходов.

### Защита веток и лимиты

`gitlab_list_protected_branches(project)` автоматически собирает все страницы
правил и возвращает `items` и `total`. Сохраняются wildcard-имена, уровни доступа
push/merge/unprotect, force push, code owners и признак наследования, если GitLab
их предоставил. Для расчёта `-protected`/`-non_protected` также проверяйте
`ci_separated_caches` через `gitlab_get_project` и контекст pipeline/ref.

`gitlab_get_application_settings()` читает `application/settings` и возвращает
только разрешённые поля в `settings`: `max_artifacts_size` (MB),
`default_artifacts_expire_in`, `keep_latest_artifact`,
`max_artifacts_content_include_size` (байты), `archive_builds_in_human_readable`,
`ci_max_total_yaml_size_bytes` (байты), `ci_max_includes`.
Отсутствующие поля перечислены в `unavailable_fields`; это **не** означает
отсутствие ограничения. Остальные настройки инсталляции не возвращаются.
При недостаточных правах инструмент явно сообщает об ошибке и необходимости
административного API-доступа. Лимиты cache Runner/хранилища и ограничения
отдельных типов artifacts из plan limits этим endpoint не предоставляются.

Контракты API: [pipelines](https://docs.gitlab.com/api/pipelines/),
[jobs](https://docs.gitlab.com/api/jobs/),
[protected branches](https://docs.gitlab.com/api/protected_branches/),
[application settings](https://docs.gitlab.com/api/settings/).

## Ограничения безопасности

- Абсолютные `destination` и `repository_path` используются точно как переданы,
  если `GITLAB_ALLOW_ABSOLUTE_PATHS=true` (значение по умолчанию). Относительные
  пути всегда разрешаются внутри первого `GITLAB_REPOSITORY_ROOTS`.
- `repository_path` должен указывать точно на корень worktree.
- Git remote должен соответствовать `GITLAB_SSH_USER@GITLAB_SSH_HOST`.
- Произвольные refspec, force-push и отключение проверки host key не
  экспортируются.
- `pull` допускает только fast-forward.
- Во время commit отключаются repository hooks. Настройки подписи Git
  наследуются из конфигурации пользователя: при `commit.gpgSign=true` MCP
  создаёт подписанный commit. Локальная настройка произвольной программы
  подписи в репозитории запрещена, чтобы репозиторий не исполнял код с правами
  MCP-процесса. Интерактивный prompt недоступен, поэтому ключ должен быть
  заранее разблокирован или доступен через соответствующий агент.
- SSH запускается с `BatchMode=yes` и `StrictHostKeyChecking=yes`.
- GitLab сам дополнительно применяет роли пользователя, protected branches,
  approval rules и остальные project policies.

## Настройка

Установка зависимостей:

```bash
cd /home/appuser/mcp/mcp-gitlab-readonly
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

Переменные окружения:

```bash
export GITLAB_URL='https://gitlab.example.com'
export GITLAB_TOKEN='glpat-...'
export GITLAB_REPOSITORY_ROOTS='/home/appuser/projects:/home/appuser/work'
export GITLAB_ALLOW_ABSOLUTE_PATHS='true'
export GITLAB_SSH_USER='git'
```

Если `GITLAB_URL` не указан, используется `https://gitlab.com`. По умолчанию
`GITLAB_SSH_HOST` равен hostname из `GITLAB_URL`; задавайте его отдельно только
если SSH обслуживается другим хостом.

Для всех REST-операций записи токену нужен scope `api`. Если нужны только
REST-чтение и Git по SSH, достаточно `read_api`; старое имя
`GITLAB_READ_TOKEN` поддерживается для совместимости.

Git clone/push не использует токен. Права определяются SSH-ключом пользователя и
его ролью в GitLab. Рекомендуется загружать ключ в `ssh-agent`; MCP должен
унаследовать `SSH_AUTH_SOCK`.

Параметр `project` в SSH-инструментах должен быть путём вида
`group/subgroup/repository`; числовой project ID и полный URL не принимаются.

Дополнительные настройки:

```text
GITLAB_SSH_PORT=22
GITLAB_SSH_IDENTITY_FILE=/absolute/path/to/key
GITLAB_SSH_KNOWN_HOSTS_FILE=/absolute/path/to/known_hosts
GITLAB_GIT_TIMEOUT_SECONDS=120
GITLAB_SSH_CONNECT_TIMEOUT_SECONDS=10
GITLAB_SSH_CONNECTION_ATTEMPTS=1
GITLAB_SSH_SERVER_ALIVE_INTERVAL_SECONDS=15
GITLAB_SSH_SERVER_ALIVE_COUNT_MAX=2
GITLAB_MCP_MAX_CONCURRENCY=8
GITLAB_MCP_MAX_GIT_CONCURRENCY=2
GITLAB_MAX_GIT_OUTPUT_CHARS=200000
GITLAB_TRACE_OUTPUT_DIR=/absolute/path/to/gitlab-traces
GITLAB_CA_BUNDLE=/absolute/path/to/ca-bundle.pem
GITLAB_TRUST_ENV_PROXY=false
```

Если `GITLAB_REPOSITORY_ROOTS` не указан, используется старый
`GITLAB_CLONE_ROOT`. Несколько корней разделяются двоеточием.

## OpenCode

```json
"gitlab": {
  "type": "local",
  "command": [
    "/home/appuser/mcp/mcp-gitlab-readonly/venv/bin/python",
    "/home/appuser/mcp/mcp-gitlab-readonly/server.py"
  ],
  "cwd": "/home/appuser/mcp/mcp-gitlab-readonly",
  "environment": {
    "GITLAB_URL": "{env:GITLAB_URL}",
    "GITLAB_TOKEN": "{env:GITLAB_TOKEN}",
    "GITLAB_REPOSITORY_ROOTS": "{env:GITLAB_REPOSITORY_ROOTS}",
    "SSH_AUTH_SOCK": "{env:SSH_AUTH_SOCK}"
  },
  "enabled": true,
  "timeout": 180000
}
```

Синхронные REST/Git-операции выполняются в worker threads, поэтому длительный
`clone`, `fetch` или `push` не блокирует event loop MCP. Общий и Git-пулы
ограничиваются отдельно переменными `GITLAB_MCP_MAX_CONCURRENCY` и
`GITLAB_MCP_MAX_GIT_CONCURRENCY`.

## Перевод существующего клона с HTTPS

Вызовите:

```text
gitlab_configure_ssh_remote(
  repository_path="/home/appuser/projects/backend",
  project="group/backend",
  remote="origin"
)
```

После этого оба значения должны быть SSH:

```bash
git -C /home/appuser/projects/backend remote get-url origin
git -C /home/appuser/projects/backend remote get-url --push origin
```

## Проверка

```bash
cd /home/appuser/mcp/mcp-gitlab-readonly
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m py_compile server.py gitlab_client.py git_ssh_client.py
```
