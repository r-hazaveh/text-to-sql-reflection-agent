"""Runnable reference SQL for the nine assessment questions shown in the assignment."""
from __future__ import annotations
import json
from pathlib import Path
from agent import execute_readonly
from setup_db import DEFAULT_DB, create_database

QUESTIONS = [
    {
        "id": 1, "level": "Easy", "question": "چه داده‌هایی در پایگاه داده وجود دارد؟",
        "sql": """SELECT name AS TableName FROM sqlite_master
                  WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                  ORDER BY name;"""
    },
    {
        "id": 2, "level": "Easy",
        "question": "نام و نام خانوادگی مشتریانی که بعد از 01-01-2021 ثبت‌نام کرده‌اند را نشان بده.",
        "sql": """SELECT CustomerID, FirstName, LastName, CreatedAt
                  FROM Customer
                  WHERE date(CreatedAt) > date('2021-01-01')
                  ORDER BY CreatedAt;"""
    },
    {
        "id": 3, "level": "Easy",
        "question": "تمام حساب‌های فعال (Active) را همراه با موجودی نشان بده.",
        "sql": """SELECT AccountID, AccountNumber, AccountType, Balance, Status
                  FROM Account WHERE Status = 'Active'
                  ORDER BY Balance DESC;"""
    },
    {
        "id": 4, "level": "Medium",
        "question": "مشتریانی را گزارش کن که حداقل یک حساب غیرفعال دارند.",
        "sql": """SELECT DISTINCT c.CustomerID, c.FirstName, c.LastName
                  FROM Customer AS c JOIN Account AS a ON a.CustomerID = c.CustomerID
                  WHERE a.Status = 'Inactive'
                  ORDER BY c.CustomerID;"""
    },
    {
        "id": 5, "level": "Medium",
        "question": "مشتری مرتبط با بزرگ‌ترین تراکنش منفرد را پیدا کن.",
        "sql": """SELECT c.CustomerID, c.FirstName, c.LastName, a.AccountNumber,
                         t.TransactionID, t.TransactionType, t.Amount, t.TransactionDate
                  FROM Customer AS c
                  JOIN Account AS a ON a.CustomerID = c.CustomerID
                  JOIN "Transaction" AS t ON t.AccountID = a.AccountID
                  ORDER BY t.Amount DESC LIMIT 1;"""
    },
    {
        "id": 6, "level": "Medium",
        "question": "مجموع واریزها (Deposit) برای حساب ACC200001 چقدر است؟",
        "sql": """SELECT a.AccountNumber, COALESCE(SUM(t.Amount), 0) AS TotalDeposits
                  FROM Account AS a
                  LEFT JOIN "Transaction" AS t
                    ON t.AccountID = a.AccountID AND t.TransactionType = 'Deposit'
                  WHERE a.AccountNumber = 'ACC200001'
                  GROUP BY a.AccountNumber;"""
    },
    {
        "id": 7, "level": "Hard",
        "question": "حساب‌هایی را فهرست کن که تمام تراکنش‌هایشان فقط از نوع Withdrawal است.",
        "sql": """SELECT a.AccountID, a.AccountNumber, COUNT(t.TransactionID) AS WithdrawalCount
                  FROM Account AS a
                  JOIN "Transaction" AS t ON t.AccountID = a.AccountID
                  GROUP BY a.AccountID, a.AccountNumber
                  HAVING COUNT(t.TransactionID) > 0
                     AND SUM(CASE WHEN t.TransactionType <> 'Withdrawal' THEN 1 ELSE 0 END) = 0
                  ORDER BY a.AccountID;"""
    },
    {
        "id": 8, "level": "Hard",
        "question": "مشتریان را بر اساس مجموع مبلغ تراکنش‌هایشان از بیشترین به کمترین مرتب کن.",
        "sql": """SELECT c.CustomerID, c.FirstName, c.LastName,
                         ROUND(COALESCE(SUM(t.Amount), 0), 2) AS TotalTransactionVolume
                  FROM Customer AS c
                  LEFT JOIN Account AS a ON a.CustomerID = c.CustomerID
                  LEFT JOIN "Transaction" AS t ON t.AccountID = a.AccountID
                  GROUP BY c.CustomerID, c.FirstName, c.LastName
                  ORDER BY TotalTransactionVolume DESC, c.CustomerID;"""
    },
    {
        "id": 9, "level": "Hard",
        "question": "مشتریانی را بیاب که هم فرستنده و هم گیرنده انتقال وجه بوده‌اند و مبالغ انتقال‌ها را نشان بده.",
        "sql": """WITH CustomerTransferRoles AS (
                      SELECT a.CustomerID,
                             MAX(CASE WHEN tr.FromAccountID = a.AccountID THEN 1 ELSE 0 END) AS IsSender,
                             MAX(CASE WHEN tr.ToAccountID = a.AccountID THEN 1 ELSE 0 END) AS IsReceiver
                      FROM Account AS a
                      JOIN Transfer AS tr
                        ON tr.FromAccountID = a.AccountID OR tr.ToAccountID = a.AccountID
                      GROUP BY a.CustomerID
                  ),
                  BothRoles AS (
                      SELECT CustomerID FROM CustomerTransferRoles
                      WHERE IsSender = 1 AND IsReceiver = 1
                  )
                  SELECT DISTINCT c.CustomerID, c.FirstName, c.LastName,
                         CASE
                           WHEN src.CustomerID = c.CustomerID AND dst.CustomerID = c.CustomerID
                             THEN 'Sender & Receiver'
                           WHEN src.CustomerID = c.CustomerID THEN 'Sender'
                           ELSE 'Receiver'
                         END AS TransferRole,
                         tr.TransferID, tr.Amount, tr.TransferDate,
                         src.AccountNumber AS FromAccountNumber,
                         dst.AccountNumber AS ToAccountNumber
                  FROM BothRoles AS br
                  JOIN Customer AS c ON c.CustomerID = br.CustomerID
                  JOIN Transfer AS tr
                  JOIN Account AS src ON src.AccountID = tr.FromAccountID
                  JOIN Account AS dst ON dst.AccountID = tr.ToAccountID
                  WHERE src.CustomerID = c.CustomerID OR dst.CustomerID = c.CustomerID
                  ORDER BY c.CustomerID, tr.TransferID;"""
    },
]

def run_all(db_path: str | Path = DEFAULT_DB) -> list[dict]:
    db_path = Path(db_path)
    if not db_path.exists():
        create_database(db_path)
    output = []
    for item in QUESTIONS:
        result = execute_readonly(db_path, item["sql"])
        output.append({
            "id": item["id"], "level": item["level"], "question": item["question"],
            "sql": item["sql"], "columns": result["columns"], "rows": result["rows"],
            "row_count": len(result["rows"]), "truncated": result["truncated"],
        })
    return output

if __name__ == "__main__":
    results = run_all()
    for item in results:
        print(f"\nQ{item['id']} [{item['level']}] — {item['question']}")
        print(f"SQL returned {item['row_count']} row(s).")
        print(json.dumps(item["rows"], ensure_ascii=False, indent=2, default=str))
