# Деплой на Railway

---

## Структура файлов

Убедитесь, что в папке проекта лежат три файла:

```
ваш-проект/
├── bitrix24_webhook.py
├── requirements.txt
└── Procfile
```

---

## Шаг 1 — Создайте аккаунт на Railway

Перейдите на [railway.app](https://railway.app) и зарегистрируйтесь через GitHub.

> ✅ Бесплатный план ($5 кредитов в месяц) полностью хватит для этого сервиса.

---

## Шаг 2 — Загрузите код на GitHub

Railway деплоит прямо из репозитория. Создайте репозиторий и залейте файлы:

```bash
git init
git add bitrix24_webhook.py requirements.txt Procfile
git commit -m "init"
git remote add origin https://github.com/ВАШ_НИК/ВАШ_РЕПО.git
git push -u origin main
```

> Если Git не установлен или не хотите использовать GitHub — в Шаге 3 есть альтернатива через Railway CLI.

---

## Шаг 3 — Создайте проект на Railway

### Вариант А — через GitHub (рекомендуется)

1. На [railway.app/dashboard](https://railway.app/dashboard) нажмите **New Project**.
2. Выберите **Deploy from GitHub repo**.
3. Найдите и выберите ваш репозиторий.
4. Railway автоматически обнаружит `Procfile` и `requirements.txt` и начнёт сборку.

### Вариант Б — через Railway CLI (без GitHub)

```bash
# Установить CLI
npm install -g @railway/cli

# Войти в аккаунт
railway login

# Инициализировать и задеплоить прямо из папки
railway init
railway up
```

---

## Шаг 4 — Задайте переменные окружения

В Railway **никогда не храните секреты в коде** — используйте переменные окружения.

1. Откройте ваш проект на Railway.
2. Перейдите в **Settings → Variables**.
3. Добавьте переменные:

| Имя переменной       | Значение                                               |
|----------------------|--------------------------------------------------------|
| `BITRIX_WEBHOOK_URL` | `https://company.bitrix24.ru/rest/1/ВАШ_ТОКЕН`        |
| `WEBHOOK_SECRET`     | Придумайте любой секрет, например `Xk9mP2qLz`         |

> Railway автоматически передаёт переменную `PORT` в контейнер — ничего дополнительно делать не нужно.

---

## Шаг 5 — Получите публичный URL

1. В дашборде Railway откройте ваш сервис.
2. Перейдите в **Settings → Networking**.
3. Нажмите **Generate Domain**.
4. Railway выдаст URL вида:

```
https://ваш-проект-production.up.railway.app
```

Это и есть адрес вашего вебхука. Полный URL для вызова:

```
https://ваш-проект-production.up.railway.app/webhook/add-product-to-deal
```

---

## Шаг 6 — Проверьте, что всё работает

### Проверка через /health

```bash
curl https://ваш-проект-production.up.railway.app/health
# Ответ: {"status": "ok"}
```

### Тестовый вызов

```bash
curl -X POST https://ваш-проект-production.up.railway.app/webhook/add-product-to-deal \
  -H "Content-Type: application/json" \
  -d '{
    "secret":   "Xk9mP2qLz",
    "deal_id":  42,
    "name":     "Тестовый товар",
    "price":    1000.00,
    "quantity": 1
  }'
```

Ожидаемый ответ:

```json
{
  "ok": true,
  "product_id": 101,
  "deal_id": 42,
  "rows_count": 1,
  "message": "Товар #101 «Тестовый товар» успешно создан и привязан к сделке #42"
}
```

---

## Автодеплой при обновлении кода

После первоначального деплоя Railway будет **автоматически пересобирать** проект
при каждом `git push` в ветку `main`. Никаких дополнительных действий не нужно.

---

## Просмотр логов

В дашборде Railway откройте ваш сервис и перейдите во вкладку **Logs** —
там в реальном времени отображаются все запросы и ошибки.

Или через CLI:

```bash
railway logs
```

---

## Итоговая схема

```
Ваш клиент (curl / Python / JS)
        │
        │  POST /webhook/add-product-to-deal
        ▼
Railway (ваш сервер)
        │
        │  REST API
        ▼
Битрикс24
  ├── crm.product.add
  ├── crm.deal.productrows.get
  └── crm.deal.productrows.set
```
