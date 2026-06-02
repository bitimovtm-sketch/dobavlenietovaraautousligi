"""
Webhook-сервер для создания товара и привязки его к сделке в Битрикс24.
Принимает вызов из автоматизации смарт-процесса, сам дозапрашивает поля
через собственный вебхук-токен.

Зависимости:
    pip install flask requests

Запуск:
    python bitrix24_webhook.py
"""

import os
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ──────────────────────────────────────────────
# НАСТРОЙКИ
# ──────────────────────────────────────────────
BITRIX_WEBHOOK_URL = os.getenv(
    "BITRIX_WEBHOOK_URL",
    "https://ВАШ_ДОМЕН.bitrix24.ru/rest/1/ВАШ_ТОКЕН"
).rstrip("/")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Поля смарт-процесса
FIELD_NAME     = "title"                          # TITLE → в API приходит строчными
FIELD_PRICE    = "ufCrm12_1779060157803"          # UF_CRM_... → camelCase в API
FIELD_QUANTITY = "ufCrm12_1780010386675"
FIELD_DEAL_ID  = "parentId2"                      # PARENT_ID_2 → camelCase

# ID типа смарт-процесса (число из DYNAMIC_1048_XX)
SMART_ENTITY_TYPE_ID = int(os.getenv("SMART_ENTITY_TYPE_ID", "1048"))
# ──────────────────────────────────────────────


def bitrix_call(method: str, params: dict) -> dict:
    """Выполняет запрос к REST API через наш вебхук-токен."""
    url = f"{BITRIX_WEBHOOK_URL}/{method}/"
    response = requests.post(url, json=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise ValueError(f"Bitrix24 error [{data['error']}]: {data.get('error_description', '')}")
    return data.get("result", data)


def get_smart_item(item_id: int) -> dict:
    """Получает поля элемента смарт-процесса по ID."""
    result = bitrix_call("crm.item.get", {
        "entityTypeId": SMART_ENTITY_TYPE_ID,
        "id": item_id,
    })
    return result.get("item", result)


def create_product(name: str, price: float, currency: str = "RUB") -> int:
    """Создаёт товар в каталоге CRM. Возвращает ID товара."""
    result = bitrix_call("crm.product.add", {"fields": {
        "NAME": name,
        "PRICE": price,
        "CURRENCY_ID": currency,
    }})
    return int(result)


def bind_product_to_deal(deal_id: int, product_id: int,
                          quantity: float, price: float,
                          currency: str = "RUB"):
    """Добавляет товар к позициям сделки, не удаляя существующие."""
    existing = bitrix_call("crm.deal.productrows.get", {"id": deal_id})
    rows = existing if isinstance(existing, list) else []
    rows.append({
        "PRODUCT_ID":  product_id,
        "PRICE":       price,
        "QUANTITY":    quantity,
        "CURRENCY_ID": currency,
    })
    bitrix_call("crm.deal.productrows.set", {"id": deal_id, "rows": rows})


def parse_number(value, default=0.0) -> float:
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


def parse_item_id(document_id_raw: str) -> int:
    """
    Разбирает document_id вида:
    ["crm","Bitrix\\Crm\\...\\Dynamic","DYNAMIC_1048_18"]
    и возвращает числовой ID элемента (18).
    """
    try:
        parsed = json.loads(document_id_raw)
    except Exception:
        parsed = document_id_raw

    if isinstance(parsed, list) and len(parsed) >= 3:
        # "DYNAMIC_1048_18" → берём последнее число
        return int(parsed[2].split("_")[-1])

    raise ValueError(f"Не удалось разобрать document_id: {document_id_raw}")


# ──────────────────────────────────────────────
# Эндпоинт для смарт-процесса
# ──────────────────────────────────────────────

@app.route("/webhook/from-smart-process", methods=["POST"])
def from_smart_process():
    """
    Принимает вызов из автоматизации смарт-процесса Битрикс24.
    Сам дозапрашивает поля через BITRIX_WEBHOOK_URL.
    """
    secret = request.args.get("secret", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "Неверный секретный ключ"}), 403

    document_id_raw = request.form.get("document_id", "")
    if not document_id_raw:
        return jsonify({"ok": False, "error": "document_id не передан"}), 400

    # Получаем ID элемента
    try:
        item_id = parse_item_id(document_id_raw)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Ошибка разбора document_id: {e}"}), 400

    # Дозапрашиваем поля смарт-процесса
    try:
        item = get_smart_item(item_id)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Не удалось получить элемент #{item_id}: {e}", "item_id": item_id}), 502

    # Извлекаем поля (API возвращает camelCase)
    name     = str(item.get(FIELD_NAME, "")).strip()
    price    = parse_number(item.get(FIELD_PRICE, 0))
    quantity = parse_number(item.get(FIELD_QUANTITY, 1)) or 1
    deal_id  = item.get(FIELD_DEAL_ID)

    # Валидация
    errors = []
    if not name:
        errors.append(f"Название товара ({FIELD_NAME}) не заполнено")
    if price <= 0:
        errors.append(f"Цена ({FIELD_PRICE}) должна быть больше 0")
    if not deal_id:
        errors.append(f"ID сделки ({FIELD_DEAL_ID}) не заполнен — привяжите сделку к смарт-процессу")
    if errors:
        return jsonify({"ok": False, "errors": errors, "item_id": item_id, "item": item}), 400

    deal_id = int(deal_id)

    try:
        product_id = create_product(name, price)
        bind_product_to_deal(deal_id, product_id, quantity, price)

        return jsonify({
            "ok": True,
            "product_id": product_id,
            "deal_id": deal_id,
            "item_id": item_id,
            "message": f"Товар «{name}» × {quantity} шт. × {price}₽ привязан к сделке #{deal_id}",
        })

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 422
    except requests.RequestException as e:
        return jsonify({"ok": False, "error": f"Ошибка сети: {e}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": f"Внутренняя ошибка: {e}"}), 500


# ──────────────────────────────────────────────
# Ручной вызов через JSON (для тестов)
# ──────────────────────────────────────────────

@app.route("/webhook/add-product-to-deal", methods=["POST"])
def add_product_to_deal():
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
        bind_product_to_deal(deal_id, product_id, quantity, price, currency)
        return jsonify({
            "ok": True,
            "product_id": product_id,
            "deal_id": deal_id,
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
