"""
Webhook-сервер для создания товара и привязки его к сделке в Битрикс24.
Принимает вызов из автоматизации смарт-процесса, сам дозапрашивает поля.

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
)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Поля смарт-процесса
FIELD_NAME     = "TITLE"
FIELD_PRICE    = "UF_CRM_12_1779060157803"
FIELD_QUANTITY = "UF_CRM_12_1780010386675"
FIELD_DEAL_ID  = "PARENT_ID_2"

# Тип смарт-процесса (число из DYNAMIC_1048_10 → entityTypeId = 1048)
SMART_ENTITY_TYPE_ID = int(os.getenv("SMART_ENTITY_TYPE_ID", "1048"))
# ──────────────────────────────────────────────


def bitrix_call(method: str, params: dict, auth_token: str = None) -> dict:
    """Выполняет запрос к REST API Битрикс24."""
    if auth_token:
        # Используем токен из входящего запроса (от автоматизации)
        domain = request._bitrix_domain
        url = f"https://{domain}/rest/{method}/"
        params["auth"] = auth_token
    else:
        url = f"{BITRIX_WEBHOOK_URL}/{method}/"

    response = requests.post(url, json=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise ValueError(f"Bitrix24 error [{data['error']}]: {data.get('error_description', '')}")
    return data.get("result", data)


def get_smart_process_item(entity_type_id: int, item_id: int, auth_token: str) -> dict:
    """Получает поля элемента смарт-процесса."""
    result = bitrix_call("crm.item.get", {
        "entityTypeId": entity_type_id,
        "id": item_id,
    }, auth_token=auth_token)
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


def parse_document_id(document_id_raw) -> int:
    """
    document_id приходит как строка или список:
    '["crm","Bitrix\\Crm\\Integration\\BizProc\\Document\\Dynamic","DYNAMIC_1048_10"]'
    Возвращает числовой ID элемента (10 в примере выше).
    """
    if isinstance(document_id_raw, str):
        try:
            document_id_raw = json.loads(document_id_raw)
        except Exception:
            pass

    if isinstance(document_id_raw, list) and len(document_id_raw) >= 3:
        last = document_id_raw[2]  # "DYNAMIC_1048_10"
        return int(last.split("_")[-1])

    raise ValueError(f"Не удалось разобрать document_id: {document_id_raw}")


def parse_auth(auth_raw) -> tuple:
    """Возвращает (access_token, domain) из поля auth."""
    if isinstance(auth_raw, str):
        try:
            auth_raw = json.loads(auth_raw)
        except Exception:
            pass
    if isinstance(auth_raw, dict):
        return auth_raw.get("access_token", ""), auth_raw.get("domain", "")
    return "", ""


# ──────────────────────────────────────────────
# Эндпоинт для смарт-процесса
# ──────────────────────────────────────────────

@app.route("/webhook/from-smart-process", methods=["POST"])
def from_smart_process():
    """
    Принимает вызов из автоматизации смарт-процесса Битрикс24.
    Битрикс присылает document_id и auth — мы сами дозапрашиваем поля.
    """
    # Секрет передаётся как ?secret=... в URL
    secret = request.args.get("secret", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "Неверный секретный ключ"}), 403

    form = request.form.to_dict()

    # Парсим auth и document_id
    auth_raw      = form.get("auth", "{}")
    document_raw  = form.get("document_id", "[]")

    try:
        auth_token, domain = parse_auth(auth_raw)
        item_id = parse_document_id(document_raw)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Ошибка разбора запроса: {e}"}), 400

    # Сохраняем домен для bitrix_call
    request._bitrix_domain = domain

    # Дозапрашиваем поля смарт-процесса
    try:
        item = get_smart_process_item(SMART_ENTITY_TYPE_ID, item_id, auth_token)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Не удалось получить данные смарт-процесса: {e}"}), 502

    # Извлекаем нужные поля
    name     = str(item.get(FIELD_NAME, "")).strip()
    price    = parse_number(item.get(FIELD_PRICE, 0))
    quantity = parse_number(item.get(FIELD_QUANTITY, 1))
    deal_id  = item.get(FIELD_DEAL_ID)

    # Валидация
    errors = []
    if not name:
        errors.append(f"Поле {FIELD_NAME} (название) не заполнено")
    if price <= 0:
        errors.append(f"Поле {FIELD_PRICE} (цена) должно быть больше 0")
    if not deal_id:
        errors.append(f"Поле {FIELD_DEAL_ID} (ID сделки) не заполнено")
    if errors:
        return jsonify({"ok": False, "errors": errors, "item": item}), 400

    deal_id = int(deal_id)

    try:
        product_id = create_product(name, price)
        bind_product_to_deal(deal_id, product_id, quantity, price)

        return jsonify({
            "ok": True,
            "product_id": product_id,
            "deal_id": deal_id,
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
