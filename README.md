gscript-1c
Этот репозиторий нужен для написания 1С в гугл таблицах

## PostgreSQL (база данных)
Теперь приложение рассчитано на PostgreSQL (SQLite оставлен только как legacy-ветка в архивировании).

### Переменные окружения
Можно задать `DATABASE_URL` напрямую или использовать набор переменных:

```
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=gscript
POSTGRES_SSLMODE=disable
```

Если задан `DATABASE_URL`, то остальные переменные не используются. Пример:

```
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/gscript
```

### Локальный запуск (консоль)
1. Установите зависимости:
   ```
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Поднимите PostgreSQL и создайте базу:
   ```
   createdb gscript
   ```
   Или через SQL:
   ```
   psql -c "CREATE DATABASE gscript;"
   ```
3. Экспортируйте переменные окружения и запустите:
   ```
   export POSTGRES_USER=postgres
   export POSTGRES_PASSWORD=postgres
   export POSTGRES_HOST=localhost
   export POSTGRES_PORT=5432
   export POSTGRES_DB=gscript
   export POSTGRES_SSLMODE=disable
   python app.py
   ```

### Развертывание на TimeWeb (задел)
1. Создайте PostgreSQL в панели TimeWeb и получите параметры подключения.
2. В переменные окружения сервера задайте:
   - `DATABASE_URL` **или** набор `POSTGRES_*`.
3. Если TimeWeb требует SSL, задайте `POSTGRES_SSLMODE=require`.

### Задел под мульти-тенантность (на будущее)
Сейчас приложение рассчитано на одну PostgreSQL-базу. Для дальнейшего разделения данных по пользователям:
- используйте отдельные схемы (`schema per tenant`) или отдельные базы данных;
- храните mapping `email -> tenant_id -> DATABASE_URL` в общей служебной БД;
- для приглашенных пользователей выдавайте доступ к одному tenant-у и при удалении отзывать его.

Синхронизация с GitHub
Сейчас удалённый репозиторий не настроен (git remote -v пуст). Если хотите видеть изменения в GitHub:

Добавьте удалённый адрес: git remote add origin https://github.com/<user>/<repo>.git.
Отправьте текущую ветку: git push -u origin work (или укажите нужное имя ветки).
После настройки git push и git pull будут синхронизировать локальные коммиты с GitHub.
