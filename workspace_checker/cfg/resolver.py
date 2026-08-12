"""MicroStation configuration file resolution.

Reproduces enough of the .cfg semantics to audit a workspace: assignment operators,
macro expansion including the path macro functions, conditional blocks, %include
graphs, and precedence tracking so shadowing can be reported.

Operators:
    =   set, overwriting any previous value (last one wins)
    :   set only if the variable is not already defined (a default)
    <   prepend to the front of the list  (searched first, so it wins)
    >   append to the end of the list     (searched last, a fallback)

Path variables such as MS_DGNLIBLIST are semicolon-separated lists searched left to
right, so position in the list is priority. ``config_verification.prepend_operator``
exists only for the unlikely case of a product build that differs.
"""

from __future__ import annotations

import glob
import logging
import os
import re
from pathlib import Path

from ..config import Settings
from ..models import ConfigModel, ConfigVar, Definition, PathMember

log = logging.getLogger(__name__)

_ASSIGN_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<op>[:+\-<>]?=|[:<>+\-])\s*(?P<value>.*)$"
)
_DIRECTIVE_RE = re.compile(r"^\s*%\s*(?P<word>[A-Za-z_]+)\s*(?P<rest>.*)$")
_MACRO_RE = re.compile(r"\$(?P<fn>[A-Za-z_]*)\((?P<arg>[^()]*)\)")
_BRACE_RE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}")
_PERCENT_RE = re.compile(r"%(?P<name>[A-Za-z_][A-Za-z0-9_]*)%")

_MAX_EXPANSION_PASSES = 24

# Folder-name -> precedence level, used when a file declares no %level.
_LEVEL_HINTS = (
    ("organizationcivil", "Organization"),
    ("organization-civil", "Organization"),
    ("organization", "Organization"),
    ("worksets", "WorkSet"),
    ("workset", "WorkSet"),
    ("workspacesetup", "WorkSpace"),
    ("workspaces", "WorkSpace"),
    ("workspace", "WorkSpace"),
    ("roles", "Role"),
    ("role", "Role"),
    ("standards", "Organization"),
    ("config", "System"),
    ("configuration", "System"),
)


def infer_level(path: Path, precedence: list[str]) -> str:
    parts = [p.lower() for p in path.parts]
    for hint, level in _LEVEL_HINTS:
        if any(hint == part or hint in part for part in parts):
            if level in precedence:
                return level
    return "System"


# --------------------------------------------------------------------------- #
# Conditional expression evaluation (no eval(); tokenised recursive descent)
# --------------------------------------------------------------------------- #
# Function-style tokens such as defined(X) must stay whole, so they precede "(".
_TOKEN_RE = re.compile(
    r"\s*(?P<tok>[A-Za-z_][A-Za-z0-9_]*\([^)]*\)|\(|\)|&&|\|\||!=|==|>=|<=|>|<|!"
    r"|\"[^\"]*\"|'[^']*'|[^\s()&|!=<>]+)"
)


def _tokenize(expr: str) -> list[str]:
    tokens, pos = [], 0
    while pos < len(expr):
        match = _TOKEN_RE.match(expr, pos)
        if not match:
            break
        tokens.append(match.group("tok"))
        pos = match.end()
    return tokens


class _ExprParser:
    def __init__(self, tokens: list[str], defined: set[str]):
        self.tokens = tokens
        self.pos = 0
        self.defined = defined

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> str | None:
        tok = self.peek()
        if tok is not None:
            self.pos += 1
        return tok

    def parse(self) -> bool:
        return self._or()

    def _or(self) -> bool:
        value = self._and()
        while self.peek() == "||":
            self.next()
            value = self._and() or value
        return value

    def _and(self) -> bool:
        value = self._not()
        while self.peek() == "&&":
            self.next()
            value = self._not() and value
        return value

    def _not(self) -> bool:
        if self.peek() == "!":
            self.next()
            return not self._not()
        return self._comparison()

    def _comparison(self) -> bool:
        left = self._atom()
        op = self.peek()
        if op in ("==", "!=", ">", "<", ">=", "<="):
            self.next()
            right = self._atom()
            return _compare(left, op, right)
        return _truthy(left)

    def _atom(self) -> str:
        tok = self.next()
        if tok is None:
            return ""
        if tok == "(":
            inner = self._or()
            if self.peek() == ")":
                self.next()
            return "1" if inner else "0"
        if tok.startswith(("'", '"')):
            return tok[1:-1]
        low = tok.lower()
        if low.startswith("defined"):
            name = _paren_arg(tok)
            return "1" if name.upper() in self.defined else "0"
        if low.startswith("exists"):
            return "1" if os.path.exists(_paren_arg(tok).strip('"')) else "0"
        return tok


def _paren_arg(token: str) -> str:
    if "(" in token and token.endswith(")"):
        return token[token.index("(") + 1 : -1].strip()
    return token


def _truthy(value: str) -> bool:
    value = (value or "").strip()
    return value not in ("", "0", "false", "False")


def _compare(left: str, op: str, right: str) -> bool:
    try:
        lnum, rnum = float(left), float(right)
        pairs = {"==": lnum == rnum, "!=": lnum != rnum, ">": lnum > rnum,
                 "<": lnum < rnum, ">=": lnum >= rnum, "<=": lnum <= rnum}
        return pairs[op]
    except ValueError:
        left, right = left.strip().lower(), right.strip().lower()
        pairs = {"==": left == right, "!=": left != right, ">": left > right,
                 "<": left < right, ">=": left >= right, "<=": left <= right}
        return pairs[op]


# --------------------------------------------------------------------------- #
# Resolver
# --------------------------------------------------------------------------- #
class CfgResolver:
    def __init__(self, settings: Settings, seed_env: dict[str, str] | None = None):
        self.settings = settings
        self.precedence = settings.precedence_levels
        self.prepend_op = settings.get(
            "config_verification", "prepend_operator", default="<"
        )
        self.max_depth = int(
            settings.get("config_verification", "max_include_depth", default=8)
        )
        self.model = ConfigModel()
        self._level_of_file: dict[str, str] = {}
        self._unresolved: set[str] = set()

        seeds = dict(settings.get("config_verification", "seed_env", default={}) or {})
        seeds.update(seed_env or {})
        for name, value in seeds.items():
            if value:
                self._set(name, "=", str(value), "CommandLine", "<seed>", 0)

    # -- public --------------------------------------------------------------- #
    def process(self, entry_points: list[str | os.PathLike]) -> ConfigModel:
        ordered = sorted(
            (Path(p) for p in entry_points),
            key=lambda p: self._rank(infer_level(p, self.precedence)),
        )
        for path in ordered:
            self.model.entry_points.append(str(path))
            self._process_file(path, infer_level(path, self.precedence), 0, [])
        self._build_path_members()
        return self.model

    # -- internals ------------------------------------------------------------ #
    def _rank(self, level: str) -> int:
        try:
            return self.precedence.index(level)
        except ValueError:
            return 0

    def _defined_names(self) -> set[str]:
        return set(self.model.variables)

    def _process_file(self, path: Path, level: str, depth: int, stack: list[str]) -> None:
        key = str(path.resolve()).lower() if path.exists() else str(path).lower()
        if key in stack:
            cycle = stack[stack.index(key) :] + [key]
            if cycle not in self.model.include_cycles:
                self.model.include_cycles.append(cycle)
            log.error("Include cycle: %s", " -> ".join(Path(c).name for c in cycle))
            return
        if depth > self.max_depth:
            self.model.parse_warnings.append(
                f"%include nesting deeper than {self.max_depth} at {path.name}"
            )
            return
        if not path.is_file():
            self.model.missing_includes.append(str(path))
            return

        self.model.max_include_depth = max(self.model.max_include_depth, depth)
        self.model.include_graph.setdefault(str(path), [])
        self._level_of_file[str(path)] = level
        stack = stack + [key]

        try:
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            self.model.parse_warnings.append(f"Cannot read {path}: {exc}")
            return

        current_level = level
        # Stack of (branch_taken, currently_active) for %if nesting.
        cond: list[list[bool]] = []
        buffer, buffer_line = "", 0

        for lineno, line in enumerate(raw.splitlines(), start=1):
            line = _strip_comment(line)
            # A bare trailing backslash is almost always a path separator, so only treat
            # it as a continuation when it is preceded by whitespace.
            if line.endswith("\\") and len(line) > 1 and line[-2] in " \t":
                if not buffer:
                    buffer_line = lineno
                buffer += line[:-1]
                continue
            if buffer:
                line, buffer = buffer + line, ""
                lineno = buffer_line
            if not line.strip():
                continue

            directive = _DIRECTIVE_RE.match(line)
            if directive:
                word = directive.group("word").lower()
                rest = directive.group("rest").strip()
                handled, current_level = self._directive(
                    word, rest, path, lineno, current_level, cond, depth, stack
                )
                if handled:
                    continue

            if cond and not all(state[1] for state in cond):
                continue

            assign = _ASSIGN_RE.match(line)
            if assign:
                op = assign.group("op").rstrip("=") or "="
                self._set(
                    assign.group("name"),
                    op,
                    assign.group("value").strip(),
                    current_level,
                    str(path),
                    lineno,
                )

    def _directive(
        self, word, rest, path, lineno, level, cond, depth, stack
    ) -> tuple[bool, str]:
        active = all(state[1] for state in cond) if cond else True

        if word == "if":
            result = active and self._eval(rest)
            cond.append([result, result])
            return True, level
        if word in ("ifdef", "ifndef"):
            name = rest.split()[0].upper() if rest.split() else ""
            present = name in self._defined_names()
            result = active and (present if word == "ifdef" else not present)
            cond.append([result, result])
            return True, level
        if word == "elif":
            if cond:
                taken = cond[-1][0]
                result = active_parent(cond) and not taken and self._eval(rest)
                cond[-1] = [taken or result, result]
            return True, level
        if word == "else":
            if cond:
                taken = cond[-1][0]
                cond[-1] = [True, active_parent(cond) and not taken]
            return True, level
        if word == "endif":
            if cond:
                cond.pop()
            return True, level
        if not active:
            return True, level

        if word == "level":
            candidate = rest.strip().strip('"')
            for known in self.precedence:
                if known.lower() == candidate.lower():
                    return True, known
            return True, level
        if word == "include":
            target = self._expand(rest.strip().strip('"'))
            resolved = self._resolve_include(target, path)
            self.model.include_graph.setdefault(str(path), []).append(str(resolved))
            self._process_file(resolved, level, depth + 1, stack)
            return True, level
        if word == "undef":
            name = rest.split()[0].upper() if rest.split() else ""
            self.model.variables.pop(name, None)
            return True, level
        if word == "lock":
            name = rest.split()[0].upper() if rest.split() else ""
            var = self.model.variables.get(name)
            if var:
                var.locked = True
            return True, level
        if word in ("error", "echo", "warn", "message", "debug"):
            return True, level
        return False, level

    def _resolve_include(self, target: str, including: Path) -> Path:
        candidate = Path(target)
        if not candidate.is_absolute():
            candidate = including.parent / candidate
        return candidate

    def _eval(self, expr: str) -> bool:
        expanded = self._expand(expr)
        try:
            return _ExprParser(_tokenize(expanded), self._defined_names()).parse()
        except (IndexError, KeyError, ValueError):
            self.model.parse_warnings.append(f"Unevaluable condition: {expr.strip()}")
            return False

    def _set(self, name, op, raw_value, level, source, lineno) -> None:
        name = name.upper()
        self._unresolved = set()
        value = self._expand(raw_value)
        var = self.model.variables.get(name)
        definition = Definition(
            name, op, raw_value, value, level, source, lineno,
            unresolved=sorted(self._unresolved),
        )

        if var is None:
            var = ConfigVar(name=name)
            self.model.variables[name] = var

        if var.locked:
            definition.applied = False
            definition.note = "ignored: variable is %locked"
            var.history.append(definition)
            return

        if op == ":":
            if var.value:
                definition.applied = False
                definition.note = "conditional set skipped: already defined"
                var.history.append(definition)
                return
        elif op in ("=", "+"):
            if var.value and self._rank(var.level) > self._rank(level):
                definition.applied = False
                definition.note = f"shadowed by higher-precedence {var.level} definition"
                var.history.append(definition)
                return

        if op == self.prepend_op:
            var.value = _join_list(value, var.value)
        elif op in ("<", ">"):
            var.value = _join_list(var.value, value)
        elif op == "+":
            members = [m for m in var.value.split(";") if m]
            if value not in members:
                var.value = _join_list(var.value, value)
        elif op == "-":
            members = [m for m in var.value.split(";") if m and m != value]
            var.value = ";".join(members)
        else:
            var.value = value

        var.level = level
        var.history.append(definition)

    def _expand(self, text: str) -> str:
        value = text or ""
        for _ in range(_MAX_EXPANSION_PASSES):
            before = value
            value = _BRACE_RE.sub(lambda m: self._lookup(m.group("name")), value)
            value = _PERCENT_RE.sub(lambda m: self._lookup(m.group("name")), value)
            value = _MACRO_RE.sub(self._expand_macro, value)
            if value == before:
                break
        return value

    def _lookup(self, name: str) -> str:
        var = self.model.variables.get(name.upper())
        if var is not None:
            return var.value
        env = os.environ.get(name)
        if env is not None:
            return env
        # An undefined reference silently collapses to "", which truncates paths; record
        # it so the checks can say "unresolved" instead of "missing".
        self._unresolved.add(name.upper())
        return ""

    def _expand_macro(self, match: re.Match) -> str:
        fn = match.group("fn").lower()
        arg = match.group("arg")
        if not fn:
            return self._lookup(arg)
        return _path_macro(fn, arg)

    def _build_path_members(self) -> None:
        self.model.path_members = []
        list_vars = set(self.settings.path_list_vars)
        for var in self.model.variables.values():
            if not var.value:
                continue
            if var.name in list_vars or ";" in var.value or _looks_like_path(var.value):
                self.model.path_members.extend(_build_path_members_for(var))


def active_parent(cond: list[list[bool]]) -> bool:
    return all(state[1] for state in cond[:-1]) if len(cond) > 1 else True


def _strip_comment(line: str) -> str:
    out, in_quote = [], ""
    for idx, ch in enumerate(line):
        if ch in ("'", '"'):
            if not in_quote:
                in_quote = ch
            elif in_quote == ch:
                in_quote = ""
        if ch == "#" and not in_quote and (idx == 0 or line[idx - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _join_list(first: str, second: str) -> str:
    parts = [p for p in (first or "").split(";") if p]
    parts += [p for p in (second or "").split(";") if p]
    seen, ordered = set(), []
    for part in parts:
        key = part.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(part)
    return ";".join(ordered)


def _path_macro(fn: str, arg: str) -> str:
    p = Path(arg) if arg else Path()
    drive = p.drive
    if fn == "dev":
        return drive + os.sep if drive else ""
    if fn == "dir":
        parent = str(p.parent)
        return parent + os.sep if parent not in ("", ".") else ""
    if fn == "file":
        return p.name
    if fn == "ext":
        return p.suffix.lstrip(".")
    if fn == "noext":
        return str(p.with_suffix("")) if p.name else arg
    if fn == "basename":
        return p.stem
    if fn == "parentdir":
        parent = str(p.parent.parent)
        return parent + os.sep if parent not in ("", ".") else ""
    if fn == "parentdev":
        return drive + os.sep if drive else ""
    return arg


# --------------------------------------------------------------------------- #
# Path member expansion
# --------------------------------------------------------------------------- #
def _build_path_members_for(var: ConfigVar) -> list[PathMember]:
    members: list[PathMember] = []
    unresolved = var.unresolved
    # A trailing separator must not produce a phantom empty member.
    for raw in [m for m in var.value.split(";") if m.strip()]:
        member = raw.strip().strip('"')
        expanded = os.path.expandvars(member)
        try:
            if any(ch in expanded for ch in "*?"):
                exists = bool(glob.glob(expanded))
            else:
                exists = os.path.exists(expanded)
        except (OSError, ValueError):
            exists = False
        source = var.history[-1] if var.history else None
        members.append(
            PathMember(
                variable=var.name,
                member=member,
                resolved=expanded,
                exists=exists,
                source_file=source.source_file if source else "",
                line=source.line if source else 0,
                unresolved=[] if exists else unresolved,
            )
        )
    return members


def _looks_like_path(value: str) -> bool:
    return any(token in value for token in ("\\", "/", ":")) and not value.startswith("$")

