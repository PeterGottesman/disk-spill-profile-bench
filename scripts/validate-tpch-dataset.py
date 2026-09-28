#!/usr/bin/env python3
"""Validate TPC-H Parquet files against tpchgen-rs metadata.

Run with Sirius's Pixi Python environment, which provides PyArrow:
    pixi run python /path/to/validate-tpch-dataset.py /raid/tpch/sf100
"""
import argparse
import json
from pathlib import Path

import pyarrow.parquet as pq

TABLES = {
    "customer", "lineitem", "nation", "orders", "part", "partsupp",
    "region", "supplier",
}


def validate(root):
    metadata = json.loads((root / "metadata.json").read_text())
    if set(metadata["tables"]) != TABLES:
        raise ValueError(f"unexpected metadata tables in {root}")
    if {path.name for path in root.iterdir() if path.is_dir()} != TABLES:
        raise ValueError(f"missing or extra table directory in {root}")

    tables = {}
    total_bytes = 0
    for table in sorted(TABLES):
        files = sorted((root / table).glob("*.parquet"))
        if not files:
            raise ValueError(f"no Parquet files for {root / table}")
        schema = None
        rows = groups = 0
        for path in files:
            footer = pq.read_metadata(path)
            if footer.num_row_groups < 1 or footer.num_rows < 1:
                raise ValueError(f"empty Parquet file: {path}")
            current_schema = footer.schema.to_arrow_schema()
            if schema is not None and not current_schema.equals(schema):
                raise ValueError(f"partition schema mismatch: {path}")
            schema = current_schema
            rows += footer.num_rows
            groups += footer.num_row_groups
            total_bytes += path.stat().st_size
        expected_rows = metadata["tables"][table]["row_count"]
        if rows != expected_rows:
            raise ValueError(f"{root / table}: {rows} rows; expected {expected_rows}")
        tables[table] = {"files": len(files), "row_groups": groups, "rows": rows}

    return {
        "path": str(root),
        "scale_factor": metadata["options"]["scale_factor"],
        "parquet_gib": round(total_bytes / 2**30, 3),
        "files": sum(table["files"] for table in tables.values()),
        "total_rows": sum(table["rows"] for table in tables.values()),
        "tables": tables,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, nargs="+")
    args = parser.parse_args()
    for root in args.dataset:
        print(json.dumps(validate(root)))


if __name__ == "__main__":
    main()
