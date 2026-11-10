import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "sortie"


def test_every_file_read_and_write_is_utf8():
    """Windows defaults to the ANSI code page; ₹, emoji and names in configs and pages would crash without this."""
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for call in re.finditer(r"\.(read_text|write_text|open)\(", text):
            depth, i = 1, call.end()
            while depth and i < len(text):  # the call's full argument list, across lines
                depth += {"(": 1, ")": -1}.get(text[i], 0)
                i += 1
            if "encoding=" not in text[call.end():i]:
                offenders.append(f"{path.name}:{text[:call.start()].count(chr(10)) + 1}")
    assert not offenders, f"file access without encoding='utf-8': {offenders}"
