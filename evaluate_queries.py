"""Run all nine reference queries and print the actual result rows."""
import json
from pathlib import Path
from evaluation_queries import run_all
from setup_db import DEFAULT_DB, create_database

if __name__ == "__main__":
    db = DEFAULT_DB
    if not db.exists():
        create_database(db)
    for item in run_all(db):
        print(f"\nQ{item['id']} [{item['level']}] — {item['question']}")
        print(f"SQL returned {item['row_count']} row(s).")
        print(json.dumps(item["rows"], ensure_ascii=False, indent=2, default=str))
