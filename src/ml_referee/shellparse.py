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
    # A line continuation inside quotes, or ``name()``, changes how words join.
    if "\\\n" in line or re.search(r"\(\s*\)", line):
        return None
    return _Lexer(line).run()


_DATA_SINKS = frozenset({"cat", "tee"})
_HEREDOC_DELIM = re.compile(r"""<<(?:'(\w+)'|"(\w+)")""")
_AFTER_OPERATOR = frozenset("|;&()`<")
# Anything that can change what ``cat`` or ``tee`` means, or what runs next.
_REDEFINES = re.compile(
    r"(?<![\w.-])(?:alias|function|eval|source|builtin|enable|trap|coproc|hash"
    r"|command|exec|PATH|BASH_ENV|ENV|shopt)(?![\w-])|(?<!\S)\.(?!\S)|\b(?:cat|tee)\s*\(",
)


def _unquoted_heredoc(line: str) -> re.Match[str] | None:
    """Return the one quoted-delimiter ``<<'WORD'`` outside quotes, or None.

    None also covers every spelling the scanner does not model: a second
    heredoc, ``<<-``, ``<<<``, an unbalanced quote, a comment, a backslash, or
    a substitution before the operator.
    """
    quote = ""
    found: re.Match[str] | None = None
    pos = 0
    while pos < len(line):
        char = line[pos]
        if quote:
            if char == quote:
                quote = ""
            elif quote == '"' and char in "\\`$":
                return None
        elif char in "'\"":
            quote = char
        elif char in "\\`#" or line.startswith("$(", pos):
            return None
        elif line.startswith("<<", pos):
            if found is not None or line.startswith(("<<<", "<<-"), pos):
                return None
            found = _HEREDOC_DELIM.match(line, pos)
            if found is None:
                return None
            pos = found.end()
            continue
        pos += 1
    return None if quote else found


def _balanced(line: str) -> bool:
    """Return True if quotes close on ``line`` and nothing reads past its end."""
    quote = ""
    pos = 0
    while pos < len(line):
        char = line[pos]
        if char == "\\" and quote != "'":
            if pos + 1 >= len(line):
                return False
            pos += 2
            continue
        if char == "`" or line.startswith("$(", pos):
            return False
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "$" and line[pos + 1 : pos + 2] in {"'", '"'}:
            return False
        pos += 1
    return not quote


def split_heredocs(text: str) -> tuple[str, list[str]] | None:  # noqa: PLR0911
    """Cut inert ``cat``/``tee`` here-document bodies out of ``text``.

    Return ``(shell, bodies)``: ``shell`` is ``text`` without each heredoc
    operator, body, and terminator line; ``bodies`` holds the body texts. A body
    is inert only when the delimiter is quoted (no expansion), the operator sits
    outside quotes, the consumer is ``cat`` or ``tee`` whose output does not
    feed a pipe or a substitution, and the terminator line exists. Return None
    when there is no heredoc or any of that is uncertain, so the caller keeps
    judging the whole text with its strictest rules.
    """
    lines = text.split("\n")
    shell: list[str] = []
    bodies: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if "<<" not in line:
            if (
                not _balanced(line)
                or _REDEFINES.search(line)
                or any(char in line for char in "(){}")
            ):
                return None
            shell.append(line)
            continue
        if _REDEFINES.search(line) or any(char in line for char in "(){}"):
            return None
        match = _unquoted_heredoc(line)
        if match is None:
            return None
        prefix, rest = line[: match.start()], line[match.end() :]
        commands = split_commands(prefix)
        if (
            not commands
            or commands[-1].words[:1] == ()
            or commands[-1].words[0] not in _DATA_SINKS
            or prefix.rstrip()[-1:] in _AFTER_OPERATOR
            or any(char in rest for char in "|`(<")
            or "$(" in rest
        ):
            return None
        delimiter = match[1] or match[2]
        body: list[str] = []
        while index < len(lines) and lines[index] != delimiter:
            body.append(lines[index])
            index += 1
        if index >= len(lines):
            return None
        index += 1
        bodies.append("\n".join(body))
        shell.append(prefix + rest)
    if not bodies:
        return None
    return "\n".join(shell), bodies
