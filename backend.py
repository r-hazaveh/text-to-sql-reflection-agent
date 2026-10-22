"""LangChain LCEL backend plus deterministic offline demo backend."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def _json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"Semantic critic did not return valid JSON: {text[:250]}")


class LangChainBackend:
    """Real model backend built from LangChain LCEL chains and ChatOpenAI."""
    uses_langchain = True

    def __init__(self, model_name: str = "gpt-4o-mini"):
        # Load the project-local .env here as well as in the CLI, so this backend
        # also works when instantiated directly from a notebook or another script.
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parent / ".env")
        except ImportError:
            pass  # Environment variables set by the shell still work without python-dotenv.

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key or api_key.lower() in {
            "replace_with_your_api_key", "paste_your_new_key_here", "your_api_key_here"
        }:
            raise RuntimeError(
                "OPENAI_API_KEY is missing or still contains the example placeholder. "
                "Copy .env.example to .env, put a valid key in that local .env file, "
                "then run again. Never paste the key into source code, reports, or chat. "
                "For an offline run use `python app.py --demo`."
            )
        try:
            from langchain_core.prompts import ChatPromptTemplate
            from langchain_core.output_parsers import StrOutputParser
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError("Install dependencies with: pip install -r requirements.txt") from exc

        self.llm = ChatOpenAI(model=model_name, temperature=0)
        parser = StrOutputParser()
        self.generate_chain = (
            ChatPromptTemplate.from_messages([
                ("system",
                 "You are a precise Text-to-SQL engineer. Generate exactly one read-only SQLite SELECT or "
                 "read-only CTE. Use only tables and columns in the supplied schema. Never write or alter data. "
                 "Return SQL only, without Markdown or explanation."),
                ("human", "Database schema and sample rows:\n{schema}\n\nQuestion:\n{question}\n\nSQL only.")
            ]) | self.llm | parser
        )
        self.repair_chain = (
            ChatPromptTemplate.from_messages([
                ("system",
                 "Repair a failed SQLite SELECT/CTE using the provided schema and exact error. "
                 "Return one read-only query only. Do not write or alter data."),
                ("human",
                 "Schema:\n{schema}\n\nQuestion:\n{question}\n\nFailed SQL:\n{sql}\n\n"
                 "SQLite error:\n{error}\n\nRepair attempt {attempt}. Return corrected SQL only.")
            ]) | self.llm | parser
        )
        self.semantic_chain = (
            ChatPromptTemplate.from_messages([
                ("system",
                 "You are a semantic critic for Text-to-SQL. In this single response, evaluate whether the query "
                 "and actual result answer the user's question. Check joins, filters, aggregation, units and "
                 "plausibility. If logically wrong, include one corrected SQLite SELECT query. Return valid JSON "
                 'only: {"is_correct": boolean, "feedback": string, "corrected_sql": string or null}. '
                 "Do not use Markdown."),
                ("human",
                 "Schema:\n{schema}\n\nQuestion:\n{question}\n\nExecuted SQL:\n{sql}\n\n"
                 "Actual result preview as JSON:\n{result_json}\n\nIf correct, corrected_sql must be null. "
                 "If incorrect, provide the best single correction in this same response.")
            ]) | self.llm | parser
        )
        self.model_name = model_name

    def generate_sql(self, question: str, schema: str) -> str:
        return self.generate_chain.invoke({"question": question, "schema": schema})

    def repair_sql(self, question: str, schema: str, sql: str, error: str, attempt: int) -> str:
        return self.repair_chain.invoke({
            "question": question, "schema": schema, "sql": sql, "error": error, "attempt": attempt
        })

    def semantic_review(self, question: str, schema: str, sql: str, result: dict[str, Any]) -> dict[str, Any]:
        response = self.semantic_chain.invoke({
            "question": question,
            "schema": schema,
            "sql": sql,
            "result_json": json.dumps(result, ensure_ascii=False, default=str),
        })
        return _json_object(response)


class DemoBackend:
    """Predictable local backend used to test reflection without an API key."""
    uses_langchain = False

    def __init__(self, scenario: str = "syntax"):
        if scenario not in {"syntax", "semantic"}:
            raise ValueError("scenario must be 'syntax' or 'semantic'")
        self.scenario = scenario
        self.generate_calls = 0
        self.repair_calls = 0
        self.semantic_calls = 0

    def generate_sql(self, question: str, schema: str) -> str:
        self.generate_calls += 1
        if self.scenario == "syntax":
            return (
                'SELECT FirstName, LastName, Balance FROM CustomerAccount '
                'WHERE Balance > 10000 ORDER BY Balance DESC;'
            )
        return (
            'SELECT c.FirstName, c.LastName, a.AccountNumber, a.Balance '
            'FROM Customer AS c JOIN Account AS a ON c.CustomerID = a.AccountID '
            'ORDER BY c.LastName;'
        )

    def repair_sql(self, question: str, schema: str, sql: str, error: str, attempt: int) -> str:
        self.repair_calls += 1
        if self.scenario == "syntax":
            return (
                'SELECT c.FirstName, c.LastName, a.AccountNumber, a.Balance '
                'FROM Customer AS c JOIN Account AS a ON c.CustomerID = a.CustomerID '
                'WHERE a.Balance > 10000 ORDER BY a.Balance DESC;'
            )
        return sql

    def semantic_review(self, question: str, schema: str, sql: str, result: dict[str, Any]) -> dict[str, Any]:
        self.semantic_calls += 1
        if self.scenario == "semantic":
            return {
                "is_correct": False,
                "feedback": (
                    "The query executes, but c.CustomerID is compared with a.AccountID instead of "
                    "a.CustomerID. Correct the join to match the foreign key."
                ),
                "corrected_sql": (
                    'SELECT c.FirstName, c.LastName, a.AccountNumber, a.Balance '
                    'FROM Customer AS c JOIN Account AS a ON c.CustomerID = a.CustomerID '
                    'ORDER BY c.LastName, c.FirstName, a.AccountID;'
                ),
            }
        return {
            "is_correct": True,
            "feedback": "The repaired query uses the Customer-Account foreign key and the requested balance filter.",
            "corrected_sql": None,
        }
