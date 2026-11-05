"""Split a shell command line into simple commands for the guard.

The lexer is deliberately small and conservative. It returns ``None`` for any
construct it cannot read with certainty (command substitution, backticks,
process substitution, here documents, comments, ``$'...'`` quoting, an
unterminated quote). The caller then applies its strictest rules.
"""

import re
from dataclasses import dataclass

_REDIRECT = re.compile(r"(?:\d+|&)?(?:>>|>&|>\||<>|<&|>|<)")
_OPERATOR = re.compile(r"&&|\|\||\|&|[;&|()\n]")
_BREAK = frozenset(" \t\r;&|()<>\n")


@dataclass(frozen=True)
class Command:
    """One simple command: its text, unquoted words, and redirections."""

    text: str
    words: tuple[str, ...]
    redirects: tuple[tuple[str, str], ...]


class _Lexer:
    def __init__(self, line: str) -> None:
        self.line = line
        self.pos = 0
        self.commands: list[Command] = []
        self.words: list[str] = []
        self.redirects: list[tuple[str, str]] = []
        self.start: int | None = None
        self.end = 0

    def _mark(self, begin: int, end: int) -> None:
        if self.start is None:
            self.start = begin
        self.end = end

    def _flush(self) -> None:
        if self.start is not None:
            self.commands.append(Command(
                self.line[self.start:self.end],
                tuple(self.words),
                tuple(self.redirects),
            ))
        self.words, self.redirects, self.start = [], [], None

    def _word(self) -> str | None:  # noqa: C901, PLR0912
        """Read one word at ``pos`` with quotes removed, or None if unreadable."""
        line, out = self.line, []
        while self.pos < len(line) and line[self.pos] not in _BREAK:
            char = line[self.pos]
            nxt = line[self.pos + 1 : self.pos + 2]
            if char == "'":
                close = line.find("'", self.pos + 1)
                if close < 0:
                    return None
                out.append(line[self.pos + 1 : close])
                self.pos = close + 1
            elif char == '"':
                self.pos += 1
                while True:
                    if self.pos >= len(line):
                        return None
                    char = line[self.pos]
                    nxt = line[self.pos + 1 : self.pos + 2]
                    if char == '"':
                        break
                    if char == "`" or (char == "$" and nxt in {"(", "'", '"'}):
                        return None
                    if char == "\\" and nxt in {'"', "\\", "$", "`"}:
                        out.append(nxt)
                        self.pos += 2
                        continue
                    out.append(char)
                    self.pos += 1
                self.pos += 1
            elif char == "`" or (char == "$" and nxt in {"(", "'", '"'}):
                return None
            elif char == "\\":
                if nxt == "\n":
                    self.pos += 2
                    continue
                out.append(nxt)
                self.pos += 2
            else:
                out.append(char)
                self.pos += 1
        return "".join(out)

    def run(self) -> list[Command] | None:
        line = self.line
        while self.pos < len(line):
            char = line[self.pos]
            if char in " \t\r":
                self.pos += 1
                continue
            if line.startswith(("<<", "<(", ">("), self.pos):
                return None
            if char == "#":
                return None
            operator = _OPERATOR.match(line, self.pos)
            redirect = _REDIRECT.match(line, self.pos)
            # ``&>`` and ``&>>`` are redirections, not a background operator.
            if redirect is not None and (
                operator is None or redirect[0].startswith("&")
            ):
                begin = self.pos
                self.pos = redirect.end()
                while self.pos < len(line) and line[self.pos] in " \t":
                    self.pos += 1
                target = self._word()
                if not target:
                    return None
                self.redirects.append((redirect[0], target))
                self._mark(begin, self.pos)
                continue
            if operator is not None:
                self._flush()
                self.pos = operator.end()
                continue
            begin = self.pos
            word = self._word()
            if word is None:
                return None
            self.words.append(word)
            self._mark(begin, self.pos)
        self._flush()
        return self.commands


def split_commands(line: str) -> list[Command] | None:
    """Split ``line`` on ``&& || ; | |& & ( )`` and newlines.

    Redirections are returned per command with their targets unquoted. Return
    None when the line holds a construct the lexer does not model.
    """
    return _Lexer(line).run()
