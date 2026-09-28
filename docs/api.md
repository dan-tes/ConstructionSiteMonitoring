# API

Интерактивная документация доступна на запущенном backend:
http://localhost:8000/docs. Там же схемы всех запросов и ответов.

## Авторизация

Frontend не отправляет пароль в открытом виде. Он хэширует его на клиенте как
`SHA-256(salt + password)` и передаёт hex-дайджест.

1. `POST /auth/salt` `{ username }` → `{ salt }`. Для несуществующего
   пользователя возвращается детерминированная HMAC-соль, так что по этому
   эндпоинту нельзя узнать, есть ли такой аккаунт.
2. `POST /auth/register` `{ username, passwordHash, salt }` → сессия.
3. `POST /auth/login` `{ username, passwordHash }` → сессия.
4. `POST /auth/logout` → `204`.
5. `GET /auth/session` → сессия, или `401`, если токен недействителен.

Сервер дополнительно хэширует дайджест bcrypt'ом. Токены хранятся в таблице
`sessions` и живут `SESSION_TTL_DAYS` дней. Токен передаётся в заголовке
`Authorization: Bearer <token>`. Ошибки имеют вид
`{ "detail": { "code", "message" } }`, коды: `VALIDATION`, `USERNAME_TAKEN`,
`INVALID_CREDENTIALS`.

## Эндпоинты

Все маршруты `/projects*` требуют токен. Пользователь видит только свои
проекты, на чужие API отвечает `404`. Закрытый проект на изменения отвечает
`409`.

| Метод | Путь | Описание |
| --- | --- | --- |
| `GET` | `/health` | проверка, что сервис жив |
| `GET` | `/projects` | проекты пользователя, новые первыми |
| `POST` | `/projects` | `{ name, description? }` |
| `GET` | `/projects/{id}` | проект с записями журнала, планом и `planStatus` |
| `PATCH` | `/projects/{id}` | `{ name?, description? }` |
| `DELETE` | `/projects/{id}` | удаляет проект вместе с файлами |
| `POST` | `/projects/{id}/plan` | multipart, поле `file`: план в `.xlsx`/`.xls`/`.csv`, заменяет прежний |
| `DELETE` | `/projects/{id}/plan` | удаляет план |
| `GET` | `/projects/{id}/plan/canonical` | `.xlsx` «план — факт»: план в каноническом формате и фактические данные из журнала |
| `GET` | `/projects/{id}/timeline` | данные для графиков отставания и диаграммы Ганта по фазам |
| `POST` | `/projects/{id}/close` | закрывает проект и сохраняет итоговый отчёт |
| `POST` | `/projects/{id}/entries` | multipart: `author`, `date` (`YYYY-MM-DD`), `comment?`, `files` (видео и фото) |
| `GET` | `/projects/{id}/entries/{entryId}` | одна запись вместе с результатами анализа, для опроса статуса |
| `DELETE` | `/projects/{id}/entries/{entryId}` | удаляет запись вместе с файлами |
| `GET` | `/files/{assetId}` | отдаёт файл. **Без авторизации**, поддерживает `Range` |

## Хранение файлов

Загруженные файлы лежат в `MEDIA_ROOT/<project_id>/<asset_id>.<ext>`. В
Docker это том `csm-media`. Метаданные файлов хранятся в таблице
`media_assets`. В ответах API ссылка на файл имеет вид
`{PUBLIC_BASE_URL}/files/{asset_id}`.

`/files/{assetId}` отдаёт файлы без авторизации, потому что теги
`<video>` и `<img>` не умеют передавать bearer-токен. Доступ защищён только
тем, что UUID нельзя угадать. Если этого окажется мало, следующим шагом
будут подписанные URL.
