"""Command-line interface for the Text-to-SQL project."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path

from agent import TextToSQLAgent
from backend import DemoBackend, LangChainBackend
from setup_db import create_database

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "bank_database.db"

def load_dotenv_if_available():
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass

def print_result(result, label: str = "RESULT"):
    print("\n" + "=" * 78)
    print(label)
    print("=" * 78)
    print(f"Question: {result.question}")
    print(f"SQL execution attempts: {result.syntax_attempts}")
    print("\nFinal SQL:\n" + result.final_sql)
    print(f"\nRows returned: {len(result.rows)}")
    if result.truncated:
        print("Note: output was truncated to the first 100 rows.")
    if result.rows:
        print(json.dumps(result.rows, ensure_ascii=False, indent=2, default=str))
    print("\nSemantic review:")
    print(json.dumps(result.semantic_review, ensure_ascii=False, indent=2, default=str))
    if result.error:
        print(f"\nFINAL ERROR: {result.error}")
    print("\nExecution trace:")
    for item in result.trace:
        print(f"  - {item}")

def run_demo(db_path: Path):
    create_database(db_path)
    syntax_question = "Show customers with accounts whose balance is above 10,000."
    syntax_result = TextToSQLAgent(
        db_path, DemoBackend("syntax"), max_syntax_attempts=3
    ).ask(syntax_question)
    print_result(syntax_result, "DEMO 1 — SYNTACTIC REFLECTION")

    semantic_question = "Show every customer's name and the balance of each of their accounts."
    semantic_result = TextToSQLAgent(
        db_path, DemoBackend("semantic"), max_syntax_attempts=3
    ).ask(semantic_question)
    print_result(semantic_result, "DEMO 2 — ONE-SHOT SEMANTIC REFLECTION")
    print("\nDemo completed. No API key was used; outputs came from the deterministic DemoBackend.")

def main():
    parser = argparse.ArgumentParser(description="Text-to-SQL with Syntactic and Semantic Reflection")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to bank_database.db")
    parser.add_argument("--question", help="Natural-language question")
    parser.add_argument("--model", default="gpt-4o-mini", help="OpenAI model name for real LLM mode")
    parser.add_argument("--max-syntax-attempts", type=int, default=3, help="Maximum total SQL execution attempts")
    parser.add_argument("--demo", action="store_true", help="Run deterministic offline demonstrations")
    parser.add_argument("--init-db", action="store_true", help="Create/reset the sample database")
    parser.add_argument("--evaluate", action="store_true", help="Run the nine reference assessment queries")
    args = parser.parse_args()
    db_path = Path(args.db).resolve()

    if args.init_db:
        create_database(db_path)
        print(f"Database initialized at: {db_path}")
        return
    if args.evaluate:
        from evaluation_queries import run_all
        for item in run_all(db_path):
            print(f"Q{item['id']}: {item['row_count']} row(s) returned")
        return
    if args.demo:
        run_demo(db_path)
        return

    load_dotenv_if_available()
    question = args.question or input("Enter a question about the banking database: ").strip()
    if not question:
        parser.error("Provide --question or enter a non-empty question.")
    if not db_path.exists():
        create_database(db_path)
    try:
        backend = LangChainBackend(model_name=args.model)
        result = TextToSQLAgent(
            db_path, backend, max_syntax_attempts=args.max_syntax_attempts
        ).ask(question)
        print_result(result, "LIVE LANGCHAIN / LLM RESULT")
    except KeyboardInterrupt:
        print("\nExecution cancelled by user.")
        return 130
    except Exception as exc:
        # Avoid ever echoing a credential if an upstream exception happens to include it.
        message = str(exc)
        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key:
            message = message.replace(api_key, "[REDACTED_API_KEY]")
        print(f"\nLive run failed ({type(exc).__name__}): {message}")
        print(
            "Check that .env is in the project folder, the key is valid and active, "
            "the account has API billing/credits, the selected model is available, "
            "and internet access is working. No key is needed for --demo or local tests."
        )
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
