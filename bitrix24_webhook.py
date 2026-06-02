"""
Webhook-сервер для добавления товара в сделку Битрикс24
по данным из смарт-процесса.

Логика:
1. Битрикс24 (бизнес-процесс смарт-процесса) шлёт POST с document_id
2. Скрипт вытаскивает ID элемента смарт-процесса
3. Через crm.item.get получает все поля элемента
4. Через crm.item.productrow.add добавляет строку товара в сделку

Зависимости:
    pip install flask requests
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

# Домен Битрикс24 для проверки (защита от чужих вызовов)
ALLOWED_DOMAIN = os.getenv("ALLOWED_DOMAIN", "autouslugi25.bitrix24.ru")

# Поля смарт-процесса (API возвращает их в camelCase)
FIELD_NAME     = "title"
FIELD_PRICE    = "ufCrm12_1779060157803"
FIELD_QUANTITY = "ufCrm12_1780010386675"
FIELD_DEAL_ID  = "parentId2"

# ID типа смарт-процесса (число из DYNAMIC_1048_XX)
SMART_ENTITY_TYPE_ID = int(os.getenv("SMART_ENTITY_TYPE_ID", "1048"))
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


def parse_form_array(form: dict, key: str):
    """
    Битрикс24 шлёт данные в PHP-формате массива:
        auth[domain]=...&auth[member_id]=...
        document_id[0]=crm&document_id[1]=...&document_id[2]=DYNAMIC_1048_28

    Эта функция собирает все ключи вида key[sub_key] в словарь или список.
    """
    prefix = f"{key}["
    items = {}
    for form_key, value in form.items():
        if form_key.startswith(prefix) and form_key.endswith("]"):
            sub_key = form_key[len(prefix):-1]
            items[sub_key] = value

    if not items:
        return None

    # Если все ключи — числа, возвращаем список
    if all(k.isdigit() for k in items.keys()):
        return [items[k] for k in sorted(items.keys(), key=int)]
    # Иначе словарь
    return items


def parse_money(value, default_currency="RUB"):
    """
    Парсит поле типа "деньги" Битрикс24: "123.45|RUB" → (123.45, "RUB").
    Возвращает кортеж (сумма, валюта).
    """
    if value is None or value == "":
        return 0.0, default_currency
    s = str(value).strip()
    if "|" in s:
        amount_str, currency = s.split("|", 1)
    else:
        amount_str, currency = s, default_currency
    try:
        amount = float(amount_str.replace(",", "."))
    except (TypeError, ValueError):
        amount = 0.0
    return amount, currency or default_currency


def parse_quantity(value, default=1.0) -> float:
    """Парсит количество — обычное число."""
    if value is None or value == "":
        return default
    try:
        result = float(str(value).replace(",", "."))
        return result if result > 0 else default
    except (TypeError, ValueError):
        return default


def parse_item_id(document_id) -> int:
    """
    Разбирает document_id и возвращает ID элемента.
    Принимает либо список (из PHP-массива), либо JSON-строку.

    Пример: ["crm","Bitrix\\Crm\\...","DYNAMIC_1048_18"] → 18
    """
    if isinstance(document_id, str):
        try:
            document_id = json.loads(document_id)
        except Exception:
            pass

    if isinstance(document_id, list) and len(document_id) >= 3:
        return int(document_id[2].split("_")[-1])

    raise ValueError(f"Не удалось разобрать document_id: {document_id}")


def check_domain(auth) -> bool:
    """Проверяет, что запрос пришёл от нашего Битрикс24."""
    if not ALLOWED_DOMAIN:
        return True
    if isinstance(auth, str):
        try:
            auth = json.loads(auth)
        except Exception:
            return False
    if not isinstance(auth, dict):
        return False
    return auth.get("domain", "") == ALLOWED_DOMAIN


# ──────────────────────────────────────────────
# Эндпоинт для смарт-процесса
# ──────────────────────────────────────────────

@app.route("/webhook/from-smart-process", methods=["POST"])
def from_smart_process():
    """
    Принимает вызов из автоматизации смарт-процесса Битрикс24.
    Добавляет строку товара в связанную сделку.
    """
    # Получаем данные из тела запроса (form-encoded от Битрикс24)
    form = request.form.to_dict()

    # Битрикс шлёт PHP-массивы: auth[domain]=..., document_id[0]=...
    # Сначала пробуем разобрать PHP-формат, если не получилось — пробуем JSON
    auth        = parse_form_array(form, "auth") or form.get("auth", "{}")
    document_id = parse_form_array(form, "document_id") or form.get("document_id", "")

    # Защита: проверяем что запрос пришёл от нашего Битрикс24
    if not check_domain(auth):
        return jsonify({
            "ok": False,
            "error": "Запрос пришёл не от разрешённого Битрикс24",
            "received_auth": auth if isinstance(auth, dict) else "не удалось разобрать",
        }), 403

    if not document_id:
        return jsonify({"ok": False, "error": "document_id не передан"}), 400

    # 1. Получаем ID элемента смарт-процесса
    try:
        item_id = parse_item_id(document_id)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Ошибка разбора document_id: {e}"}), 400

    # 2. Получаем поля элемента смарт-процесса
    try:
        result = bitrix_call("crm.item.get", {
            "entityTypeId": SMART_ENTITY_TYPE_ID,
            "id": item_id,
        })
        item = result.get("item", result)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Не удалось получить элемент #{item_id}: {e}"}), 502

    # 3. Извлекаем нужные поля
    name           = str(item.get(FIELD_NAME, "")).strip()
    price, currency = parse_money(item.get(FIELD_PRICE))
    quantity       = parse_quantity(item.get(FIELD_QUANTITY))
    deal_id        = item.get(FIELD_DEAL_ID)

    # Валидация
    errors = []
    if not name:
        errors.append(f"Название товара ({FIELD_NAME}) не заполнено")
    if price <= 0:
        errors.append(f"Цена ({FIELD_PRICE}) должна быть больше 0 (получено: {item.get(FIELD_PRICE)})")
    if not deal_id:
        errors.append(f"ID сделки ({FIELD_DEAL_ID}) не заполнен — привяжите сделку к смарт-процессу")
    if errors:
        return jsonify({"ok": False, "errors": errors, "item_id": item_id}), 400

    deal_id = int(deal_id)

    # 4. Добавляем строку товара в сделку через современный метод
    try:
        result = bitrix_call("crm.item.productrow.add", {
            "fields": {
                "ownerType":    "D",          # D = Deal (сделка)
                "ownerId":      deal_id,
                "productName":  name,
                "price":        price,
                "quantity":     quantity,
            }
        })
        row_id = result.get("productRow", {}).get("id") if isinstance(result, dict) else None

        return jsonify({
            "ok":          True,
            "row_id":      row_id,
            "deal_id":     deal_id,
            "item_id":     item_id,
            "name":        name,
            "price":       price,
            "quantity":    quantity,
            "currency":    currency,
            "message":     f"Товар «{name}» × {quantity} × {price}{currency} добавлен в сделку #{deal_id}",
        })

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 422
    except requests.RequestException as e:
        return jsonify({"ok": False, "error": f"Ошибка сети: {e}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": f"Внутренняя ошибка: {e}"}), 500


# ──────────────────────────────────────────────
# Эндпоинт для ручного вызова (тесты)
# ──────────────────────────────────────────────

@app.route("/webhook/add-product-to-deal", methods=["POST"])
def add_product_to_deal():
    """
    Ручной вызов через JSON:
    {
        "deal_id":  123,
        "name":     "Название",
        "price":    9900,
        "quantity": 2
    }
    """
    data = request.get_json(silent=True) or {}

    missing = [f for f in ("deal_id", "name", "price") if f not in data]
    if missing:
        return jsonify({"ok": False, "error": f"Отсутствуют поля: {', '.join(missing)}"}), 400

    try:
        result = bitrix_call("crm.item.productrow.add", {
            "fields": {
                "ownerType":   "D",
                "ownerId":     int(data["deal_id"]),
                "productName": str(data["name"]),
                "price":       float(data["price"]),
                "quantity":    float(data.get("quantity", 1)),
            }
        })
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"Сервер запущен на порту {port}")
    app.run(host="0.0.0.0", port=port)
