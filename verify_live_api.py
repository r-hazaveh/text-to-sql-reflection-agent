"""Verify the real OpenAI API connection and optionally run an end-to-end Text-to-SQL check.

API key is read from .env or the environment; it is never printed or saved.
Run:
    python verify_live_api.py --smoke-only
    python verify_live_api.py
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SMOKE_PROMPT = "Reply with exactly this token and nothing else: TEXT_TO_SQL_API_OK"

def redact_secret(message: str, secret: str | None) -> str:
    if secret:
        return message.replace(secret, "[REDACTED_API_KEY]")
    return message

def load_project_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("Missing dependency: python-dotenv. Run: python -m pip install -r requirements.txt")
        raise SystemExit(2)
    load_dotenv(BASE_DIR / ".env", override=False)

def main() -> int:
    parser = argparse.ArgumentParser(description="Verify OpenAI API and Text-to-SQL live mode.")
    parser.add_argument("--model", default="gpt-4o-mini", help="OpenAI model to verify")
    parser.add_argument("--smoke-only", action="store_true",
                        help="Only verify a minimal API response; skip the full Text-to-SQL agent.")
    args = parser.parse_args()
    load_project_env()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or api_key in {"PASTE_YOUR_NEW_KEY_HERE", "your_api_key_here"}:
        print("API CHECK: NOT RUN")
        print("OPENAI_API_KEY is missing or still set to the example placeholder.")
        print("Create .env beside this script and add a NEW valid API key.")
        return 2
    try:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=args.model, temperature=0, timeout=30, max_retries=1)
        response = llm.invoke(SMOKE_PROMPT)
        content = response.content
        if isinstance(content, list):
            content = " ".join(str(item.get("text", "")) if isinstance(item, dict) else str(item)
                               for item in content)
        content = str(content).strip()
        if "TEXT_TO_SQL_API_OK" not in content:
            print("API CHECK: FAILED")
            print("The endpoint returned a response, but it did not match the expected token.")
            print(f"Model selected: {args.model}")
            return 1
        print("API CHECK: PASSED")
        print(f"Model: {args.model}")
        print("A real request reached the model and returned the expected token.")
        if args.smoke_only:
            return 0
        from agent import TextToSQLAgent
        from backend import LangChainBackend
        from setup_db import create_database
        db_path = BASE_DIR / "bank_database.db"
        if not db_path.exists():
            create_database(db_path)
        backend = LangChainBackend(model_name=args.model)
        result = TextToSQLAgent(db_path, backend, max_syntax_attempts=3).ask(
            "List all active bank accounts with the customer's first name, last name, "
            "account number, and balance. Sort by balance from highest to lowest."
        )
        if result.error:
            print("END-TO-END TEXT-TO-SQL: FAILED")
            print(f"Error: {redact_secret(result.error, api_key)}")
            return 1
        if not result.rows or not result.columns:
            print("END-TO-END TEXT-TO-SQL: FAILED")
            print("The agent executed but returned no rows or columns for the smoke-test question.")
            print(f"SQL: {result.final_sql}")
            return 1
        print("END-TO-END TEXT-TO-SQL: PASSED")
        print(f"SQL execution attempts: {result.syntax_attempts}")
        print(f"Returned rows: {len(result.rows)}")
        print(f"Columns: {', '.join(result.columns)}")
        print("Final SQL:")
        print(result.final_sql)
        print("Semantic review:")
        print(result.semantic_review.get("feedback", "(no feedback returned)"))
        print("No API key was printed.")
        return 0
    except KeyboardInterrupt:
        print("\nAPI check cancelled.")
        return 130
    except Exception as exc:
        print("LIVE API CHECK: FAILED")
        print(f"Error type: {type(exc).__name__}")
        print(f"Details: {redact_secret(str(exc), api_key)}")
        print("Check key validity, API billing/credits, model access, and internet connectivity.")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
