"""
Webhook-сервер для создания товара и привязки его к сделке в Битрикс24.

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
# НАСТРОЙКИ — замените на свои значения
# ──────────────────────────────────────────────
BITRIX_WEBHOOK_URL = os.getenv(
    "BITRIX_WEBHOOK_URL",
    "https://ВАШ_ДОМЕН.bitrix24.ru/rest/1/ВАШ_ТОКЕН"
)
# Секретный ключ для защиты вашего вебхука (опционально)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
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


def create_product(name: str, price: float, currency: str = "RUB", **extra) -> int:
    """
    Создаёт товар в каталоге Битрикс24 (crm.product.add).

    Возвращает ID созданного товара.
    """
    fields = {
        "NAME": name,
        "PRICE": price,
        "CURRENCY_ID": currency,
        **extra,           # любые дополнительные поля: DESCRIPTION, SECTION_ID, …
    }
    result = bitrix_call("crm.product.add", {"fields": fields})
    return int(result)     # API возвращает ID напрямую


def bind_product_to_deal(deal_id: int, product_id: int, quantity: float = 1,
                          price: float = None, currency: str = "RUB") -> list:
    """
    Привязывает товар к сделке через crm.deal.productrows.set.

    Если price не передан, подтягивается цена из карточки товара.
    Возвращает список товарных позиций после установки.
    """
    # Получаем текущие товарные позиции сделки, чтобы не затереть существующие
    existing = bitrix_call("crm.deal.productrows.get", {"id": deal_id})
    rows = existing if isinstance(existing, list) else []

    # Если цена не задана явно — берём из карточки товара
    if price is None:
        product_info = bitrix_call("crm.product.get", {"id": product_id})
        price = float(product_info.get("PRICE", 0))

    new_row = {
        "PRODUCT_ID": product_id,
        "PRODUCT_NAME": None,   # Битрикс подставит имя сам по PRODUCT_ID
        "PRICE": price,
        "QUANTITY": quantity,
        "CURRENCY_ID": currency,
    }
    rows.append(new_row)

    result = bitrix_call("crm.deal.productrows.set", {"id": deal_id, "rows": rows})
    return result


# ──────────────────────────────────────────────
# Эндпоинт вебхука
# ──────────────────────────────────────────────

@app.route("/webhook/add-product-to-deal", methods=["POST"])
def add_product_to_deal():
    """
    Принимает JSON и создаёт товар, привязывая его к сделке.

    Ожидаемый JSON:
    {
        "secret":      "ВАШ_СЕКРЕТ",          // опционально
        "deal_id":     123,                    // обязательно
        "name":        "Название товара",      // обязательно
        "price":       9900.00,               // обязательно
        "quantity":    2,                      // опционально, по умолчанию 1
        "currency":    "RUB",                  // опционально, по умолчанию RUB
        "description": "Описание товара"       // опционально
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": False, "error": "Тело запроса должно быть JSON"}), 400

    # Проверка секретного ключа (если задан)
    if WEBHOOK_SECRET and data.get("secret") != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "Неверный секретный ключ"}), 403

    # Валидация обязательных полей
    missing = [f for f in ("deal_id", "name", "price") if f not in data]
    if missing:
        return jsonify({"ok": False, "error": f"Отсутствуют поля: {', '.join(missing)}"}), 400

    deal_id  = int(data["deal_id"])
    name     = str(data["name"])
    price    = float(data["price"])
    quantity = float(data.get("quantity", 1))
    currency = str(data.get("currency", "RUB"))

    # Дополнительные поля для карточки товара
    extra = {}
    if "description" in data:
        extra["DESCRIPTION"] = data["description"]

    try:
        # 1. Создаём товар
        product_id = create_product(name, price, currency, **extra)

        # 2. Привязываем к сделке
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
