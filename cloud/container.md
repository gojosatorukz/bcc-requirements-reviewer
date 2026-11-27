# Полное рабочее место в Docker

Образ запускает тот же Python-агент и тот же интерфейс, что и команда `python -m reviewer.serve`. Внутри есть сохранённый официальный прогон, исполняемые примеры, диалог с владельцем и его перепроверка.

## Открыть на своём компьютере

В чистом клоне соберите версию с идентификатором коммита для паспорта будущих прогонов. Для локальной сборки с незакоммиченными изменениями опустите `--build-arg`.

```sh
docker build --build-arg VCS_REF="$(git rev-parse HEAD)" -t razbor-tz:local .
docker run --rm --init -p 127.0.0.1:8765:8765 razbor-tz:local python -m cloud.runtime
```

Откройте [рабочее место](http://127.0.0.1:8765). Ссылка предназначена для браузера на этом компьютере. Если порт занят, используйте, например, `-p 127.0.0.1:18765:18765` и `python -m cloud.runtime --port 18765`. Проверка Host и Origin требует совпадения портов снаружи и внутри контейнера. После остановки контейнера новые временные прогоны удаляются. Если результат нужен позднее, скачайте JSON или HTML. Из HTML можно напечатать PDF средствами браузера.

Перепроверить сохранённые расчёты можно без сети и учётной записи.

```sh
docker run --rm --network none razbor-tz:local python -m reviewer.executable replay runs/bcc-release/review.json
```

## Проверить новый документ

Для профиля Gemini укажите в локальном файле `.env` переменные `REVIEW_PROVIDER=gemini`, `REVIEW_MODEL=gemini-3.5-flash-lite` и `GEMINI_API_KEY`. Docker прочитает их при старте.

```sh
docker run --rm --init --env-file .env -p 127.0.0.1:8765:8765 razbor-tz:local python -m cloud.runtime
```

### Профиль Astra

Понадобится собственный доступ к модели. Следующая команда принимает личный ключ OpenAI из переменной окружения в вашей оболочке через стандартный ввод. Codex хранит его в отдельном Docker-томе. Копировать авторизацию с другого компьютера не нужно.

```sh
printenv OPENAI_API_KEY | docker run --rm -i -v razbor-codex-auth:/home/reviewer/.codex razbor-tz:local codex login --with-api-key
docker run --rm -v razbor-codex-auth:/home/reviewer/.codex razbor-tz:local codex login status
docker run --rm --init -p 127.0.0.1:8765:8765 -v razbor-codex-auth:/home/reviewer/.codex razbor-tz:local python -m cloud.runtime
```

В рабочем месте загрузите документ, дождитесь окончания проверки и скачайте отчёт. По умолчанию запрашивается `gpt-6.2-astra`. Если в вашем API-проекте доступна другая совместимая модель, передайте её имя в переменной `REVIEW_MODEL` контейнера. Использование ключа расходует средства его владельца. Для удаления авторизации выполните `codex logout` в контейнере с тем же томом.

CLI можно запустить отдельно. Поместите тестовый документ в `./input/requirements.md`. Каталог `./output` должен разрешать запись пользователю контейнера с UID 10001.

```sh
mkdir -p output
docker run --rm --init -v razbor-codex-auth:/home/reviewer/.codex \
  -v "$PWD/input:/input:ro" -v "$PWD/output:/output" \
  razbor-tz:local python -m reviewer /input/requirements.md /output/review.json
```

Штатный сервер по умолчанию слушает loopback внутри контейнера. Модуль `cloud.runtime` включают только для локального проброса порта, показанного выше. Для сайта с отдельной сессией для каждого посетителя используйте облачную точку входа `python -m cloud.public` за HTTPS-прокси. [Публикация для жюри](hosting.md)
