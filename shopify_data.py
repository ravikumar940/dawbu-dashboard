"""
Fetch sales (last N days of orders) and current stock from Shopify.

Settings are read from environment variables (set them in Render -> Environment):
  SHOPIFY_STORE          e.g. dawbu.myshopify.com
  and EITHER
  SHOPIFY_ACCESS_TOKEN   an Admin API token (starts with shpat_)
  OR
  SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET   from a Dev Dashboard app installed on your store

Required app scopes: read_orders, read_products, read_inventory
"""

import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

API_VERSION = "2025-10"

ORDERS_QUERY = """
query($cursor: String, $q: String!) {
  orders(first: 25, after: $cursor, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      createdAt
      cancelledAt
      lineItems(first: 30) {
        nodes {
          title
          quantity
          product { title }
          originalUnitPriceSet { shopMoney { amount } }
        }
      }
    }
  }
}
"""

PRODUCTS_QUERY = """
query($cursor: String) {
  products(first: 100, after: $cursor, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    nodes { title totalInventory tracksInventory }
  }
}
"""


def is_configured():
    return bool(os.getenv("SHOPIFY_STORE")) and bool(
        os.getenv("SHOPIFY_ACCESS_TOKEN")
        or (os.getenv("SHOPIFY_CLIENT_ID") and os.getenv("SHOPIFY_CLIENT_SECRET"))
    )


def _store():
    store = os.getenv("SHOPIFY_STORE", "").strip()
    store = store.replace("https://", "").replace("http://", "").strip("/")
    if not store.endswith(".myshopify.com"):
        store = f"{store}.myshopify.com"
    return store


def _token():
    token = os.getenv("SHOPIFY_ACCESS_TOKEN", "").strip()
    if token:
        return token
    # Dev Dashboard app: exchange client id + secret for a short-lived token
    resp = requests.post(
        f"https://{_store()}/admin/oauth/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": os.getenv("SHOPIFY_CLIENT_ID", "").strip(),
            "client_secret": os.getenv("SHOPIFY_CLIENT_SECRET", "").strip(),
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Shopify login failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()["access_token"]


def _graphql(token, query, variables):
    resp = requests.post(
        f"https://{_store()}/admin/api/{API_VERSION}/graphql.json",
        json={"query": query, "variables": variables},
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Shopify API error ({resp.status_code}): {resp.text[:300]}")
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"Shopify API error: {str(body['errors'])[:300]}")
    return body["data"]


def fetch(days=30):
    """Return (sales_df, stock_df) in the same format as the CSV files."""
    token = _token()

    since = (datetime.now(timezone.utc) - timedelta(days=days + 1)).strftime("%Y-%m-%d")
    rows, cursor = [], None
    while True:
        data = _graphql(token, ORDERS_QUERY, {"cursor": cursor, "q": f"created_at:>={since}"})
        orders = data["orders"]
        for order in orders["nodes"]:
            if order.get("cancelledAt"):
                continue
            day = order["createdAt"][:10]
            for item in order["lineItems"]["nodes"]:
                name = (item.get("product") or {}).get("title") or item["title"]
                price = float(item["originalUnitPriceSet"]["shopMoney"]["amount"] or 0)
                rows.append({"date": day, "product": name,
                             "units_sold": item["quantity"], "price": price})
        if not orders["pageInfo"]["hasNextPage"]:
            break
        cursor = orders["pageInfo"]["endCursor"]

    sales = pd.DataFrame(rows, columns=["date", "product", "units_sold", "price"])

    stock_rows, cursor = [], None
    while True:
        data = _graphql(token, PRODUCTS_QUERY, {"cursor": cursor})
        products = data["products"]
        for p in products["nodes"]:
            if p.get("tracksInventory") is False:
                continue
            stock_rows.append({"product": p["title"], "current_stock": max(p["totalInventory"] or 0, 0)})
        if not products["pageInfo"]["hasNextPage"]:
            break
        cursor = products["pageInfo"]["endCursor"]

    stock = pd.DataFrame(stock_rows, columns=["product", "current_stock"])
    stock = stock.groupby("product", as_index=False)["current_stock"].sum()
    return sales, stock
