"""Safe Text-to-SQL agent with bounded syntactic and one-shot semantic reflection."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Protocol

from setup_db import create_database


class SQLSafetyError(ValueError):
    """Raised when generated SQL is not a safe, read-only query."""


class LLMBackend(Protocol):
    def generate_sql(self, question: str, schema: str) -> str: ...
    def repair_sql(self, question: str, schema: str, sql: str, error: str, attempt: int) -> str: ...
    def semantic_review(self, question: str, schema: str, sql: str, result: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class AgentResult:
    question: str
    final_sql: str
    rows: list[dict[str, Any]]
    columns: list[str]
    syntax_attempts: int
    semantic_review: dict[str, Any]
    trace: list[str]
    error: str | None = None
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_sql(text: str) -> str:
    """Remove Markdown code fences and any short preamble before SELECT/WITH."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("The model returned an empty SQL statement.")
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:sql)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    match = re.search(r"\b(SELECT|WITH)\b", cleaned, flags=re.IGNORECASE)
    if match:
        cleaned = cleaned[match.start():]
    return cleaned.strip()


def get_schema_context(db_path: str | Path, use_langchain: bool = False) -> str:
    """Get DDL using LangChain SQLDatabase in live mode, with SQLite fallback."""
    path = Path(db_path).resolve()
    if use_langchain:
        try:
            from sqlalchemy import create_engine
            from langchain_community.utilities import SQLDatabase
            engine = create_engine(f"sqlite:///{path.as_posix()}")
            try:
                return SQLDatabase(engine, sample_rows_in_table_info=2).get_table_info()
            finally:
                engine.dispose()
        except Exception:
            # The app remains usable if optional schema introspection fails.
            pass
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    return "\n\n".join(row[0] for row in rows if row[0])


def _readonly_authorizer(action, arg1, arg2, db_name, trigger_name):
    allowed = {
        getattr(sqlite3, "SQLITE_SELECT", -1),
        getattr(sqlite3, "SQLITE_READ", -2),
        getattr(sqlite3, "SQLITE_FUNCTION", -3),
        getattr(sqlite3, "SQLITE_RECURSIVE", -4),
    }
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def execute_readonly(db_path: str | Path, sql: str, max_rows: int = 100) -> dict[str, Any]:
    """Run exactly one SELECT/CTE; SQLite authorizer blocks writes and schema changes."""
    statement = extract_sql(sql).strip()
    if not re.match(r"^(SELECT|WITH)\b", statement, flags=re.IGNORECASE):
        raise SQLSafetyError("Only SELECT queries and read-only CTEs are allowed.")
    statement = statement.rstrip().rstrip(";").strip()
    if ";" in statement:
        raise SQLSafetyError("Multiple SQL statements are not allowed.")
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.set_authorizer(_readonly_authorizer)
    try:
        cursor = connection.execute(statement)
        raw_rows = cursor.fetchmany(max_rows + 1)
        truncated = len(raw_rows) > max_rows
        raw_rows = raw_rows[:max_rows]
        columns = [col[0] for col in (cursor.description or [])]
        rows = [dict(row) for row in raw_rows]
        return {"columns": columns, "rows": rows, "truncated": truncated}
    finally:
        connection.close()


def parse_review(review: Any) -> dict[str, Any]:
    """Normalize semantic critic output returned as a dict or JSON string."""
    if isinstance(review, dict):
        parsed = review
    else:
        text = str(review).strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                return {
                    "is_correct": True,
                    "feedback": "Semantic response was not valid JSON; automatic correction was skipped.",
                    "corrected_sql": None,
                    "review_parse_error": True,
                }
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {
                    "is_correct": True,
                    "feedback": "Semantic response JSON was invalid; automatic correction was skipped.",
                    "corrected_sql": None,
                    "review_parse_error": True,
                }
    return {
        "is_correct": bool(parsed.get("is_correct", True)),
        "feedback": str(parsed.get("feedback", "")),
        "corrected_sql": parsed.get("corrected_sql"),
        "review_parse_error": False,
    }


class TextToSQLAgent:
    """Bounded Syntactic Reflection followed by a single Semantic Reflection call."""

    def __init__(self, db_path: str | Path, backend: LLMBackend, max_syntax_attempts: int = 3):
        self.db_path = Path(db_path).resolve()
        self.backend = backend
        self.max_syntax_attempts = max(1, int(max_syntax_attempts))
        if not self.db_path.exists():
            create_database(self.db_path)

    def ask(self, question: str) -> AgentResult:
        trace: list[str] = []
        schema = get_schema_context(
            self.db_path, use_langchain=bool(getattr(self.backend, "uses_langchain", False))
        )
        sql = extract_sql(self.backend.generate_sql(question, schema))
        trace.extend(["Generated the initial SQL query.", f"Initial SQL: {sql}"])
        execution = None
        syntax_attempts = 0

        while syntax_attempts < self.max_syntax_attempts:
            syntax_attempts += 1
            try:
                execution = execute_readonly(self.db_path, sql)
                trace.append(f"Syntactic attempt {syntax_attempts}: query executed successfully.")
                break
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                trace.append(f"Syntactic attempt {syntax_attempts}: {error}")
                if syntax_attempts >= self.max_syntax_attempts:
                    trace.append("Syntactic retry limit reached; stopping safely.")
                    return AgentResult(
                        question=question, final_sql=sql, rows=[], columns=[],
                        syntax_attempts=syntax_attempts,
                        semantic_review={
                            "is_correct": False,
                            "feedback": "Semantic review skipped because SQL did not execute.",
                            "corrected_sql": None,
                        },
                        trace=trace, error=error,
                    )
                sql = extract_sql(
                    self.backend.repair_sql(question, schema, sql, error, syntax_attempts)
                )
                trace.extend([
                    f"Syntactic Reflection requested repair {syntax_attempts}.",
                    f"SQL after syntactic repair: {sql}",
                ])

        assert execution is not None
        review = parse_review(self.backend.semantic_review(question, schema, sql, execution))
        trace.append("Semantic Reflection completed once.")
        final_sql, final_execution, final_error = sql, execution, None

        corrected_sql = review.get("corrected_sql")
        if not review.get("is_correct", True) and corrected_sql:
            corrected_sql = extract_sql(str(corrected_sql))
            if corrected_sql != sql:
                trace.append("Semantic critic found a likely logical issue; executing one corrected query.")
                trace.append(f"Semantic correction SQL: {corrected_sql}")
                try:
                    final_execution = execute_readonly(self.db_path, corrected_sql)
                    final_sql = corrected_sql
                    trace.append("Semantic correction executed successfully; no further semantic loop.")
                except Exception as exc:
                    final_sql = corrected_sql
                    final_execution = {"columns": [], "rows": [], "truncated": False}
                    final_error = f"{type(exc).__name__}: {exc}"
                    trace.append("Semantic correction failed; no retry was performed by design.")
        elif not review.get("is_correct", True):
            trace.append("Semantic critic flagged a concern but supplied no corrected SQL.")

        return AgentResult(
            question=question, final_sql=final_sql,
            rows=final_execution["rows"], columns=final_execution["columns"],
            syntax_attempts=syntax_attempts, semantic_review=review,
            trace=trace, error=final_error, truncated=final_execution.get("truncated", False),
        )
