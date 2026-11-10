"""Your own answers to screening questions, kept in config/answers.yaml.

Exact rewordings are reused directly; the whole book also goes to the LLM so reworded questions get the
same answer. The file is append-only from the bot's side, so your comments and edits survive.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml

from sortie.config import CONFIG_DIR

DEFAULT_PATH = CONFIG_DIR / "answers.yaml"

HEADER = """\
# Answers you gave to screening questions.
# The bot reuses an answer when the same question comes up again, and gives this whole file to the AI
# so reworded questions get the same answer. Edit freely: change an answer, delete an entry, or add your own
# (question + answer; options are optional).
"""


def question_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class AnswerBook:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        self.entries: list[dict] = []
        if path.exists():
            self.entries = [e for e in (yaml.safe_load(path.read_text(encoding="utf-8")) or []) if e.get("question")]

    def lookup(self, question: str) -> str | list[str] | None:
        key = question_key(question)
        for entry in reversed(self.entries):  # latest answer wins
            if question_key(entry["question"]) == key:
                return entry["answer"]
        return None

    def add(self, question: str, answer: str | list[str], options: list[str] | None = None) -> None:
        entry = {"question": question, "answer": answer}
        if options:
            entry["options"] = options
        entry["answered"] = date.today().isoformat()
        self.entries.append(entry)
        if not self.path.exists():
            self.path.write_text(HEADER, encoding="utf-8")
        with self.path.open("a", encoding="utf-8") as f:
            f.write("\n" + yaml.safe_dump([entry], sort_keys=False, allow_unicode=True))

    def as_prompt(self) -> str:
        """Previous answers for the LLM prompt; empty when there are none."""
        if not self.entries:
            return ""
        lines = [f"- Q: {e['question']}\n  A: {e['answer']}" for e in self.entries]
        return "CANDIDATE'S OWN PREVIOUS ANSWERS (reuse these for the same or reworded questions):\n" + "\n".join(lines)
