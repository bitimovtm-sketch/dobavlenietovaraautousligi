"""
Webhook-сервер для создания товара и привязки его к сделке в Битрикс24.
Поддерживает два режима:
  1. Ручной вызов (JSON с явными полями)
  2. Автоматизация смарт-процесса Битрикс24 (данные из полей смарт-процесса)

Зависимости:
    pip install flask requests

Запуск:
    python bitrix24_webhook.py
"""

import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ──────────────────────────────────────────────
# НАСТРОЙКИ
# ──────────────────────────────────────────────
BITRIX_WEBHOOK_URL = os.getenv(
    "BITRIX_WEBHOOK_URL",
    "https://ВАШ_ДОМЕН.bitrix24.ru/rest/1/ВАШ_ТОКЕН"
)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Поля смарт-процесса — названия из Битрикс24
FIELD_NAME     = "TITLE"                          # Название товара
FIELD_PRICE    = "UF_CRM_12_1779060157803"        # Цена
FIELD_QUANTITY = "UF_CRM_12_1780010386675"        # Количество
FIELD_AMOUNT   = "UF_CRM_12_1780369914186"        # Сумма (цена × кол-во)
FIELD_DEAL_ID  = "PARENT_ID_2"                    # ID связанной сделки
# ──────────────────────────────────────────────


def bitrix_call(method: str, params: dict) -> dict:
    """Выполняет запрос к REST API Битрикс24."""
    url = f"{BITRIX_WEBHOOK_URL}/{method}/"
    response = requests.post(url, json=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise ValueError(f"Bitrix24 error [{data['error']}]: {data.get('error_description', '')}")
    return data.get("result", data)


def create_product(name: str, price: float, currency: str = "RUB") -> int:
    """Создаёт товар в каталоге CRM. Возвращает ID товара."""
    fields = {
        "NAME": name,
        "PRICE": price,
        "CURRENCY_ID": currency,
    }
    result = bitrix_call("crm.product.add", {"fields": fields})
    return int(result)


def bind_product_to_deal(deal_id: int, product_id: int,
                          quantity: float, price: float,
                          currency: str = "RUB") -> list:
    """Добавляет товар к позициям сделки, не удаляя существующие."""
    existing = bitrix_call("crm.deal.productrows.get", {"id": deal_id})
    rows = existing if isinstance(existing, list) else []

    rows.append({
        "PRODUCT_ID":   product_id,
        "PRICE":        price,
        "QUANTITY":     quantity,
        "CURRENCY_ID":  currency,
    })

    result = bitrix_call("crm.deal.productrows.set", {"id": deal_id, "rows": rows})
    return result


def parse_number(value, default=0.0) -> float:
    """Безопасно конвертирует значение в float."""
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


# ──────────────────────────────────────────────
# Эндпоинт для смарт-процесса Битрикс24
# ──────────────────────────────────────────────

@app.route("/webhook/from-smart-process", methods=["POST"])
def from_smart_process():
    """
    Принимает данные от автоматизации смарт-процесса Битрикс24.

    Битрикс отправляет форму (form-encoded), поэтому читаем request.form.
    Поля берутся из смарт-процесса:
      - TITLE             → название товара
      - UF_CRM_12_...     → цена, количество, сумма
      - PARENT_ID_2       → ID связанной сделки
    """
    # Битрикс шлёт данные как form-data внутри ключа 'data'
    # Пробуем оба варианта: form и JSON
    raw = request.form.to_dict() or {}
    if not raw:
        raw = request.get_json(silent=True) or {}

    # Битрикс часто оборачивает поля в data[FIELDS][...]
    # Достаём данные откуда они есть
    fields = {}
    for key, val in raw.items():
        # Пример ключа: data[FIELDS][TITLE]
        if key.startswith("data[FIELDS]"):
            field_name = key.replace("data[FIELDS][", "").rstrip("]")
            fields[field_name] = val
        else:
            fields[key] = val

    # Проверка секрета (передаётся как query-параметр ?secret=...)
    secret = request.args.get("secret", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "Неверный секретный ключ"}), 403

    # Извлекаем нужные поля
    name     = fields.get(FIELD_NAME, "").strip()
    price    = parse_number(fields.get(FIELD_PRICE, 0))
    quantity = parse_number(fields.get(FIELD_QUANTITY, 1))
    deal_id  = fields.get(FIELD_DEAL_ID, "")

    # Валидация
    errors = []
    if not name:
        errors.append("Название товара (TITLE) не заполнено")
    if price <= 0:
        errors.append("Цена должна быть больше 0")
    if not deal_id:
        errors.append("ID сделки (PARENT_ID_2) не передан")
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    deal_id = int(deal_id)

    try:
        product_id = create_product(name, price)
        bind_product_to_deal(deal_id, product_id, quantity, price)

        return jsonify({
            "ok": True,
            "product_id": product_id,
            "deal_id": deal_id,
            "message": f"Товар «{name}» (×{quantity} × {price}₽) привязан к сделке #{deal_id}",
        })

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 422
    except requests.RequestException as e:
        return jsonify({"ok": False, "error": f"Ошибка сети: {e}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": f"Внутренняя ошибка: {e}"}), 500


# ──────────────────────────────────────────────
# Эндпоинт для ручного вызова (как раньше)
# ──────────────────────────────────────────────

@app.route("/webhook/add-product-to-deal", methods=["POST"])
def add_product_to_deal():
    """
    Ручной вызов через JSON:
    {
        "secret":   "...",
        "deal_id":  123,
        "name":     "Название товара",
        "price":    9900.00,
        "quantity": 2
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": False, "error": "Тело запроса должно быть JSON"}), 400

    if WEBHOOK_SECRET and data.get("secret") != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "Неверный секретный ключ"}), 403

    missing = [f for f in ("deal_id", "name", "price") if f not in data]
    if missing:
        return jsonify({"ok": False, "error": f"Отсутствуют поля: {', '.join(missing)}"}), 400

    deal_id  = int(data["deal_id"])
    name     = str(data["name"])
    price    = float(data["price"])
    quantity = float(data.get("quantity", 1))
    currency = str(data.get("currency", "RUB"))

    try:
        product_id = create_product(name, price, currency)
        rows = bind_product_to_deal(deal_id, product_id, quantity, price, currency)

        return jsonify({
            "ok": True,
            "product_id": product_id,
            "deal_id": deal_id,
            "rows_count": len(rows) if isinstance(rows, list) else None,
            "message": f"Товар #{product_id} «{name}» успешно создан и привязан к сделке #{deal_id}",
        })

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 422
    except requests.RequestException as e:
        return jsonify({"ok": False, "error": f"Ошибка сети: {e}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": f"Внутренняя ошибка: {e}"}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    print(f"Сервер запущен на порту {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
