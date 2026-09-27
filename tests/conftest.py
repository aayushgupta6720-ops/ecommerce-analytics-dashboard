import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_frame(rows: list[tuple]) -> pd.DataFrame:
    """rows: (invoice, 'YYYY-MM-DD HH:MM', stock_code, quantity, price, customer_id, country)."""
    df = pd.DataFrame(rows, columns=["invoice", "invoice_date", "stock_code", "quantity", "price",
                                     "customer_id", "country"])
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    df["description"] = "ITEM " + df["stock_code"]
    df["revenue"] = df["quantity"] * df["price"]
    df["customer_id"] = df["customer_id"].astype("Int64")
    df["is_cancellation"] = df["invoice"].str.startswith("C")
    df["is_product"] = df["stock_code"].str.match(r"^\d{5}[A-Z]*$")
    for col in ["invoice", "stock_code", "description", "country"]:
        df[col] = df[col].astype("category")
    return df


@pytest.fixture
def tiny() -> pd.DataFrame:
    # 4 sales invoices, 1 cancellation, 1 postage line, 1 guest (no customer id).
    return make_frame([
        ("1001", "2010-01-04 10:00", "10001", 2, 5.0, 1, "United Kingdom"),   # 10
        ("1001", "2010-01-04 10:00", "10002", 1, 20.0, 1, "United Kingdom"),  # 20
        ("1001", "2010-01-04 10:00", "POST", 1, 15.0, 1, "United Kingdom"),   # non-product
        ("1002", "2010-01-20 14:00", "10001", 4, 5.0, 2, "France"),           # 20
        ("1003", "2010-02-10 09:00", "10001", 1, 5.0, 1, "United Kingdom"),   # 5
        ("1003", "2010-02-10 09:00", "10003", 3, 10.0, 1, "United Kingdom"),  # 30
        ("1004", "2010-02-15 11:00", "10002", 2, 20.0, None, "Germany"),      # 40, guest
        ("C1005", "2010-02-16 12:00", "10002", -1, 20.0, 1, "United Kingdom"),  # -20
    ])
