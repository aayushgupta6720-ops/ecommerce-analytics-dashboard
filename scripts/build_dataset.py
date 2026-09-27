"""Download UCI Online Retail II and write a clean Parquet file for the dashboard.

Usage:
    python scripts/build_dataset.py            # download (if needed), clean, write parquet
    python scripts/build_dataset.py --xlsx PATH  # use a local copy of online_retail_II.xlsx
    python scripts/build_dataset.py --feather-only  # just convert the committed parquet (Render build step)

Parsing the 1M-row xlsx takes a few minutes, so the raw sheets are cached to
data/raw/raw.parquet after the first run. Delete it to force a re-parse.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow.feather as feather
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from retail.countries import DISPLAY_NAMES  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"
OUT_PATH = ROOT / "data" / "processed" / "transactions.parquet"
FEATHER_PATH = OUT_PATH.with_suffix(".feather")  # gitignored; the app memory-maps it
ZIP_URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
ZIP_PATH = RAW_DIR / "online_retail_ii.zip"
XLSX_NAME = "online_retail_II.xlsx"
RAW_CACHE = RAW_DIR / "raw.parquet"
SHEETS = ["Year 2009-2010", "Year 2010-2011"]

# Real merchandise codes are five digits plus an optional letter suffix (85123A, 15056BL).
# Everything else is postage, fees, discounts, manual adjustments, samples or vouchers.
PRODUCT_CODE = r"^\d{5}[A-Z]*$"


def log(msg: str) -> None:
    print(msg, flush=True)


def ensure_xlsx(xlsx_arg: str | None) -> Path:
    if xlsx_arg:
        return Path(xlsx_arg)
    xlsx = RAW_DIR / XLSX_NAME
    if xlsx.exists():
        return xlsx
    if not ZIP_PATH.exists():
        log(f"Downloading {ZIP_URL}")
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(ZIP_URL, ZIP_PATH)
    with zipfile.ZipFile(ZIP_PATH) as zf:
        zf.extract(XLSX_NAME, RAW_DIR)
    return xlsx


def load_raw(xlsx_arg: str | None) -> pd.DataFrame:
    if RAW_CACHE.exists() and not xlsx_arg:
        log(f"Using cached raw sheets at {RAW_CACHE.relative_to(ROOT)}")
        return pd.read_parquet(RAW_CACHE)
    xlsx = ensure_xlsx(xlsx_arg)
    frames = []
    for sheet in SHEETS:
        log(f"Parsing sheet '{sheet}' (slow, a few minutes)...")
        df = pd.read_excel(
            xlsx,
            sheet_name=sheet,
            engine="openpyxl",
            dtype={"Invoice": str, "StockCode": str, "Description": str, "Country": str},
        )
        df["sheet"] = sheet
        frames.append(df)
    raw = pd.concat(frames, ignore_index=True)
    raw.to_parquet(RAW_CACHE, index=False)
    return raw


def clean(raw: pd.DataFrame) -> pd.DataFrame:
    log(f"\nRaw rows: {len(raw):,}")
    for sheet, part in raw.groupby("sheet"):
        log(f"  {sheet}: {len(part):,} rows, {part['InvoiceDate'].min()} -> {part['InvoiceDate'].max()}")

    df = raw.rename(
        columns={
            "Invoice": "invoice",
            "StockCode": "stock_code",
            "Description": "description",
            "Quantity": "quantity",
            "InvoiceDate": "invoice_date",
            "Price": "price",
            "Customer ID": "customer_id",
            "Country": "country",
        }
    ).drop(columns="sheet")

    df["invoice"] = df["invoice"].astype(str).str.strip()
    df["stock_code"] = df["stock_code"].astype(str).str.strip().str.upper()
    df["description"] = df["description"].str.strip()
    df["country"] = df["country"].str.strip()

    before = len(df)
    df = df.drop_duplicates()
    log(f"Dropped {before - len(df):,} exact duplicate rows (sheet overlap + repeated lines) -> {len(df):,}")

    # Each rule is evaluated on what's left after the previous one, so the counts don't overlap.
    drops = {
        "bad-debt adjustment ('A') invoices": lambda d: d["invoice"].str.startswith("A"),
        "price <= 0": lambda d: d["price"] <= 0,
        "negative quantity outside a cancellation (stock write-offs)":
            lambda d: ~d["invoice"].str.startswith("C") & (d["quantity"] < 0),
        "positive quantity on a cancellation": lambda d: d["invoice"].str.startswith("C") & (d["quantity"] > 0),
    }
    for label, rule in drops.items():
        mask = rule(df)
        df = df[~mask]
        log(f"Dropped {int(mask.sum()):,} rows: {label} -> {len(df):,}")

    df["is_cancellation"] = df["invoice"].str.startswith("C")
    df["is_product"] = df["stock_code"].str.match(PRODUCT_CODE)

    # One canonical description per stock code: the most common one on real sales lines.
    sales = df[~df["is_cancellation"] & df["description"].notna()]
    canonical = (
        sales.groupby(["stock_code", "description"]).size().reset_index(name="n")
        .sort_values(["stock_code", "n", "description"], ascending=[True, False, True])
        .drop_duplicates("stock_code")
        .set_index("stock_code")["description"]
    )
    df["description"] = df["stock_code"].map(canonical).fillna(df["description"]).fillna("UNKNOWN")

    df["customer_id"] = df["customer_id"].astype("Int64")
    df["country"] = df["country"].replace(DISPLAY_NAMES)
    df["revenue"] = (df["quantity"] * df["price"]).round(2)

    for col in ["invoice", "stock_code", "description", "country"]:
        df[col] = df[col].astype("category")
    df["quantity"] = df["quantity"].astype("int32")
    df["invoice_date"] = pd.to_datetime(df["invoice_date"]).astype("datetime64[s]")

    cols = ["invoice", "invoice_date", "stock_code", "description", "quantity", "price",
            "revenue", "customer_id", "country", "is_cancellation", "is_product"]
    return df[cols].sort_values("invoice_date", kind="stable").reset_index(drop=True)


def summarize(df: pd.DataFrame) -> None:
    sales = df[~df["is_cancellation"] & df["is_product"]]
    cancels = df[df["is_cancellation"]]
    log("\nSummary")
    log(f"  rows:              {len(df):,}")
    log(f"  date range:        {df['invoice_date'].min()} -> {df['invoice_date'].max()}")
    log(f"  sales invoices:    {sales['invoice'].nunique():,}")
    log(f"  cancel invoices:   {cancels['invoice'].nunique():,}")
    log(f"  customers:         {sales['customer_id'].nunique():,}")
    log(f"  no-customer lines: {sales['customer_id'].isna().mean():.1%} of product sales lines")
    log(f"  products:          {sales['stock_code'].nunique():,}")
    log(f"  countries:         {df['country'].nunique():,}")
    log(f"  gross product sales: £{sales['revenue'].sum():,.2f}")
    log(f"  cancelled value:     £{-cancels['revenue'].sum():,.2f}")
    non_product = df.loc[~df["is_product"], "stock_code"].value_counts().head(12)
    log(f"  top non-product codes: {', '.join(f'{k} ({v:,})' for k, v in non_product.items())}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xlsx", help="path to a local online_retail_II.xlsx")
    parser.add_argument("--feather-only", action="store_true",
                        help="skip the rebuild; write the uncompressed Arrow copy of the existing parquet")
    args = parser.parse_args()

    if args.feather_only:
        write_feather(pq.read_table(OUT_PATH))
        return

    df = clean(load_raw(args.xlsx))
    summarize(df)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PATH, index=False, compression="zstd")
    log(f"\nWrote {OUT_PATH.relative_to(ROOT)} ({OUT_PATH.stat().st_size / 1e6:.1f} MB)")
    write_feather(pq.read_table(OUT_PATH))


def write_feather(table) -> None:
    feather.write_feather(table, FEATHER_PATH, compression="uncompressed")
    log(f"Wrote {FEATHER_PATH.relative_to(ROOT)} ({FEATHER_PATH.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
