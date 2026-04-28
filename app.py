
from flask import Flask, request, jsonify, send_from_directory, Response
import requests
import time
import re
import threading
from typing import List, Dict, Any, Optional
import os
from dotenv import load_dotenv, dotenv_values
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

print("ENV PATH:", ENV_PATH)

# 🔬 bypass os.environ completely for debugging
raw_env = dotenv_values(ENV_PATH)
print("RAW ENV PARSE:", raw_env)

load_dotenv(ENV_PATH)

print("POST LOAD ORG:", os.getenv("ZOHO_ORG_ID"))
print("DEBUG ORG:", os.getenv("ZOHO_ORG_ID"))
print("DEBUG CLIENT:", os.getenv("ZOHO_CLIENT_ID"))
app = Flask(__name__, static_folder="static", static_url_path="")

# =========================================================
# 🔐 ENV CONFIG
# =========================================================

ORG_ID = os.getenv("ZOHO_ORG_ID")
ZOHO_BASE_URL = "https://www.zohoapis.com/inventory/v1/items"
ZOHO_GROUP_URL = "https://www.zohoapis.com/inventory/v1/itemgroups"
TOKEN_FILE = "token.txt"

REQUIRED_ENV = [
    "ZOHO_ORG_ID",
    "ZOHO_REFRESH_TOKEN",
    "ZOHO_CLIENT_ID",
    "ZOHO_CLIENT_SECRET"
]

missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
if missing:
    raise Exception(f"Missing environment variables: {', '.join(missing)}")


# =========================================================
# 🧠 TOKEN CACHE
# =========================================================

_current_token = None
_token_last_read = 0
TOKEN_CACHE_TTL = 300


def get_access_token():
    global _current_token, _token_last_read

    now = time.time()
    if _current_token and (now - _token_last_read < TOKEN_CACHE_TTL):
        return _current_token

    try:
        with open(TOKEN_FILE, "r") as f:
            token = f.read().strip()
            if token:
                _current_token = token
                _token_last_read = now
                return token
    except FileNotFoundError:
        pass

    return os.getenv("ZOHO_ACCESS_TOKEN", "")


def refresh_access_token():
    global _current_token, _token_last_read

    print("🔄 Refreshing Zoho token...")

    url = "https://accounts.zoho.com/oauth/v2/token"
    data = {
        "refresh_token": os.getenv("ZOHO_REFRESH_TOKEN"),
        "client_id": os.getenv("ZOHO_CLIENT_ID"),
        "client_secret": os.getenv("ZOHO_CLIENT_SECRET"),
        "grant_type": "refresh_token"
    }

    try:
        resp = requests.post(url, data=data, timeout=15)
        resp.raise_for_status()

        token = resp.json().get("access_token")
        if not token:
            raise Exception("No access_token returned")

        with open(TOKEN_FILE, "w") as f:
            f.write(token)

        _current_token = token
        _token_last_read = time.time()

        print("✅ Token refreshed")
        return token

    except Exception as e:
        print(f"❌ Token refresh failed: {e}")
        return None


def zoho_get(url, params=None, timeout=15):
    token = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}

    params = params or {}
    params["organization_id"] = ORG_ID

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)

        if resp.status_code == 401:
            print("🔁 Token expired, retrying...")
            new_token = refresh_access_token()

            if not new_token:
                return resp

            headers["Authorization"] = f"Zoho-oauthtoken {new_token}"
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)

        return resp

    except Exception as e:
        print(f"❌ Request error: {e}")

        class Dummy:
            status_code = 500
            text = str(e)

            def json(self):
                return {}

        return Dummy()


# =========================================================
# 💾 CACHE LAYER
# =========================================================

cache = {"items": [], "last_updated": 0, "loading": False, "error": None}

CACHE_TTL = 600

image_binary_cache = {}
IMAGE_BINARY_TTL = 3600

gallery_cache = {}
GALLERY_CACHE_TTL = 1800


# =========================================================
# 📦 DATA FETCH
# =========================================================

def fetch_all_items_from_zoho() -> List[Dict[str, Any]]:
    all_items = []
    page = 1
    per_page = 200

    while True:
        resp = zoho_get(ZOHO_BASE_URL, params={"page": page, "per_page": per_page})

        if resp.status_code != 200:
            print(f"🚨 Zoho API error: {resp.status_code}")
            break

        data = resp.json()
        batch = data.get("items", [])

        if not batch:
            break

        all_items.extend(batch)

        if len(batch) < per_page:
            break

        page += 1

    print(f"✅ Fetched {len(all_items)} items")
    return all_items


# =========================================================
# 🧬 TRANSFORM
# =========================================================

def extract_customers(raw: Dict[str, Any]):
    customers = []

    for k, v in raw.items():
        if k.startswith("cf_") and v:
            if "customer" in k.lower() or "data2" in k.lower():
                customers.append({"label": k, "value": v})

    return customers


def transform_item(raw: Dict[str, Any]):
    def safe_int(x):
        try:
            return int(x) if x is not None else 0
        except:
            return 0

    def safe_float(x):
        try:
            return float(x) if x is not None else 0.0
        except:
            return 0.0

    return {
        "item_id": raw.get("item_id", ""),
        "group_id": raw.get("group_id", ""),
        "name": raw.get("name") or "Unnamed Product",
        "sku": raw.get("sku") or "",
        "mpn": raw.get("part_number") or "",
        "manufacturer": raw.get("manufacturer") or raw.get("manufacturer_name") or "",
        "customers": extract_customers(raw),
        "rate": safe_float(raw.get("rate")),
        "stock": safe_int(raw.get("available_stock")),
        "image_document_id": raw.get("image_document_id", "")
    }


# =========================================================
# 🔄 CACHE REFRESH
# =========================================================

def refresh_cache():
    global cache

    if cache["loading"]:
        return

    cache["loading"] = True

    try:
        raw = fetch_all_items_from_zoho()
        cache["items"] = [transform_item(i) for i in raw]
        cache["last_updated"] = time.time()
        cache["error"] = None

        print(f"🔄 Cache updated: {len(cache['items'])}")

    except Exception as e:
        cache["error"] = str(e)
        print(f"❌ Cache error: {e}")

    finally:
        cache["loading"] = False


def get_cached_items():
    if not cache["items"] or (time.time() - cache["last_updated"] > CACHE_TTL):
        refresh_cache()
    return cache["items"]


# =========================================================
# 🔍 SEARCH ENGINE
# =========================================================

def compute_relevance(item, query):
    q = query.lower()

    score = 0
    sku = item.get("sku", "").lower()
    name = item.get("name", "").lower()
    mpn = item.get("mpn", "").lower()
    manufacturer = item.get("manufacturer", "").lower()

    if sku == q: score += 100
    if sku.startswith(q): score += 70
    if name.startswith(q): score += 60
    if manufacturer.startswith(q): score += 55

    if q in sku: score += 50
    if q in name: score += 40
    if q in mpn: score += 35
    if q in manufacturer: score += 30

    for c in item.get("customers", []):
        if q in c["value"].lower():
            score += 25
            break

    return score


def search_local(query: str):
    if not query:
        return []

    items = get_cached_items()
    matches = []

    for item in items:
        haystack = " ".join([
            item.get("sku", ""),
            item.get("name", ""),
            item.get("mpn", ""),
            item.get("manufacturer", "")
        ]).lower()

        if query.lower() in haystack:
            matches.append(item)

    ranked = sorted(
        [(compute_relevance(i, query), i) for i in matches],
        key=lambda x: x[0],
        reverse=True
    )

    return [i for _, i in ranked[:10]]


# =========================================================
# 🖼 IMAGE ROUTES
# =========================================================

def fetch_attachment_binary(item_id, attachment_id):
    url = f"{ZOHO_BASE_URL}/{item_id}/attachments/{attachment_id}"
    resp = zoho_get(url)
    return resp.content if resp.status_code == 200 else None


@app.route("/api/item-image/<item_id>")
def item_image(item_id):
    key = f"img_{item_id}"

    if key in image_binary_cache and time.time() - image_binary_cache[key]["ts"] < IMAGE_BINARY_TTL:
        return Response(image_binary_cache[key]["data"], mimetype="image/jpeg")

    url = f"{ZOHO_BASE_URL}/{item_id}/image"
    resp = zoho_get(url)

    if resp.status_code == 200:
        image_binary_cache[key] = {"data": resp.content, "ts": time.time()}
        return Response(resp.content, mimetype="image/jpeg")

    return "", 404


# =========================================================
# 🌐 ROUTES
# =========================================================

@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"count": 0, "items": []})

    results = search_local(q)

    for r in results:
        r["main_image_url"] = f"/api/item-image/{r['item_id']}"

    return jsonify({"count": len(results), "items": results})


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "cached_items": len(cache["items"])
    })


# =========================================================
# 🚀 START
# =========================================================

if __name__ == "__main__":
    if not get_access_token():
        print("⚠️ No access token available")

    threading.Thread(target=refresh_cache, daemon=True).start()
    app.run(debug=True, host="0.0.0.0", port=5000)