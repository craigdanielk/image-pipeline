#!/usr/bin/env python3
"""NODE: fetch-data
Refresh data/products.json from the matrix (product_master + product_label_content),
with metafields derived in the canonical-target style. Thin wrapper over lib so the
join + derivation logic lives in exactly one place (lib.build_products).

Usage: python fetch_data.py
"""
import os, json
import lib

OUT = os.path.join(lib.HERE, "data", "products.json")


def main():
    products = lib.build_products()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"products": products}, f, indent=2, ensure_ascii=False)
    print(f"wrote {len(products)} products -> {OUT}")
    needs = [p["slug"] for p in products
             if "NEEDS" in p["image_state"].upper() or p["image_state"] in ("", "NONE")]
    print(f"  needs-generation: {len(needs)}")


if __name__ == "__main__":
    main()
