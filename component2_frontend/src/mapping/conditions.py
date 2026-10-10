"""PHP condition -> TypeScript, by whitelist only (docs/generation-spec.md section 6).

A condition is translated only if every part of it is on the spec's whitelist:
the Item's row fields that are in the item type, comparison operators, logical
operators, number and string literals, isset() of a row field, and an R-I2
list guard's own variable. Anything else (a superglobal, a function call,
another variable, a field the contract does not have) makes the whole
condition untranslatable; the caller then emits unresolvedCondition().

The parser follows PHP's precedence (`!` > relational > equality > `&&` > `||`
> `and` > `or`) and the output adds parentheses where TypeScript's precedence
would read it differently (PHP `$a and $b || $c` is `a && (b || c)`).

Type rules, so the output compiles and means what the PHP meant:
  - a comparison needs both sides of one type (number, string or boolean).
    A string compared with a number is loose_compare_semantics: PHP and
    JavaScript differ there and TypeScript rejects it, so it is not translated;
  - a value used as a truth value must be a number or a boolean: PHP treats
    the string "0" as false and JavaScript as true, so a string field is not
    used as a truth value.
"""

import re
from dataclasses import dataclass

from src.mapping.jsx import js_string as _js_string
from src.model import SchemaType

NUMBER_KINDS = ("number", "integer")
_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<var>\$[A-Za-z_]\w*)
  | (?P<float>\d+\.\d+)
  | (?P<int>\d+)
  | (?P<sq>'(?:[^'\\]|\\.)*')
  | (?P<dq>"(?:[^"\\]|\\.)*")
  | (?P<op>===|!==|==|!=|<>|<=|>=|&&|\|\||[<>!()\[\],])
  | (?P<word>[A-Za-z_]\w*)
  | (?P<other>.)
""", re.X | re.S)
# PHP binding power, weakest first; comparisons do not chain.
_BINARY = {"or": 1, "and": 2, "||": 3, "&&": 4, "==": 5, "!=": 5, "===": 5, "!==": 5,
           "<": 6, ">": 6, "<=": 6, ">=": 6}
_NOT = 7
# TypeScript precedence of what each node becomes.
_TS = {"||": 4, "&&": 5, "==": 9, "!=": 9, "===": 9, "!==": 9, "<": 10, ">": 10, "<=": 10, ">=": 10}
_TS_UNARY = 14
_TS_PRIMARY = 20


class NotTranslatable(Exception):
    def __init__(self, reason: str = "condition_not_translatable"):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Scope:
    """What a condition may refer to where it stands."""

    row_var: str | None = None  # the Item's PHP row variable, e.g. "$row"; None outside an Item
    row_fields: tuple[tuple[str, SchemaType], ...] | None = None  # the item type; None when unknown
    row_code: str = "row"
    guard_var: str | None = None  # an R-I2 guard's list variable (the loop's iterExpr), e.g. "$comments"
    items_code: str = "items"


@dataclass(frozen=True)
class Translation:
    code: str | None  # the TypeScript expression; None when the condition is not translated
    reason: str | None  # why not (condition_unavailable, condition_not_translatable, needs_auth_context, ...)


@dataclass
class _Node:
    code: str
    ts: int  # TypeScript precedence of `code`
    type: str  # number | string | boolean | unknown
    nullable: bool = False


def _tokens(text: str) -> list[tuple[str, str]]:
    out = []
    for found in _TOKEN.finditer(text):
        kind = found.lastgroup
        if kind == "ws":
            continue
        if kind == "other":
            raise NotTranslatable()
        value = found.group()
        if kind == "word" and value.lower() in ("and", "or"):
            kind, value = "op", value.lower()
        out.append((kind, value))
    return out


def _php_single_quoted(literal: str) -> str:
    return re.sub(r"\\([\\'])", r"\1", literal[1:-1])


def _ts_property(base: str, name: str) -> str:
    return f"{base}.{name}" if re.fullmatch(r"[A-Za-z_$][\w$]*", name) else f'{base}["{name}"]'


class _Parser:
    def __init__(self, tokens, scope: Scope):
        self.tokens, self.scope, self.i = tokens, scope, 0

    def peek(self, offset=0):
        return self.tokens[self.i + offset] if self.i + offset < len(self.tokens) else (None, None)

    def take(self, value=None):
        token = self.peek()
        if token[0] is None or (value is not None and token[1] != value):
            raise NotTranslatable()
        self.i += 1
        return token

    def parse(self) -> _Node:
        node = self.binary(0)
        if self.peek()[0] is not None:
            raise NotTranslatable()
        return node

    def binary(self, weakest: int) -> _Node:
        left = self.unary()
        while True:
            kind, op = self.peek()
            power = _BINARY.get(op) if kind == "op" else None
            if power is None or power <= weakest:
                return left
            self.take()
            right = self.binary(power)  # left-associative
            if power >= 5 and self.peek()[0] == "op" and _BINARY.get(self.peek()[1]) == power:
                raise NotTranslatable()  # `$a == $b == $c` is not valid PHP
            left = self.combine(op, left, right)

    def unary(self) -> _Node:
        kind, value = self.peek()
        if (kind, value) == ("op", "!"):
            self.take()
            operand = self.truth(self.binary(_NOT))
            return _Node("!" + self.wrap(operand, _TS_UNARY), _TS_UNARY, "boolean")
        return self.primary()

    def primary(self) -> _Node:
        kind, value = self.take()
        if (kind, value) == ("op", "("):
            inner = self.binary(0)
            self.take(")")
            return _Node(f"({inner.code})", _TS_PRIMARY, inner.type, inner.nullable)
        if kind == "int":
            if len(value) > 1 and value.startswith("0"):
                raise NotTranslatable()  # PHP reads a leading 0 as octal
            return _Node(value, _TS_PRIMARY, "number")
        if kind == "float":
            return _Node(value, _TS_PRIMARY, "number")
        if kind == "sq":
            return _Node(_js_string(_php_single_quoted(value)), _TS_PRIMARY, "string")
        if kind == "dq":
            if "$" in value or "\\" in value:
                raise NotTranslatable()  # interpolation or escapes: not a plain literal
            return _Node(_js_string(value[1:-1]), _TS_PRIMARY, "string")
        if kind == "word" and value.lower() == "isset":
            self.take("(")
            field = self.row_field()
            self.take(")")
            return _Node(f"({field.code} !== undefined && {field.code} !== null)", _TS_PRIMARY, "boolean")
        if kind == "var":
            self.i -= 1
            return self.variable()
        raise NotTranslatable()

    def row_field(self) -> _Node:
        kind, name = self.take()
        scope = self.scope
        if kind != "var" or name != scope.row_var or scope.row_fields is None:
            raise NotTranslatable()
        self.take("[")
        key_kind, key = self.take()
        if key_kind == "sq":
            key = _php_single_quoted(key)
        elif key_kind == "dq" and "$" not in key and "\\" not in key:
            key = key[1:-1]
        else:
            raise NotTranslatable()
        self.take("]")
        fields = dict(scope.row_fields)
        if key not in fields:
            raise NotTranslatable()  # not in the item type: never guessed
        schema = fields[key]
        kind_ = "number" if schema.kind in NUMBER_KINDS else schema.kind
        if kind_ not in ("number", "string", "boolean"):
            raise NotTranslatable()
        return _Node(_ts_property(scope.row_code, key), _TS_PRIMARY, kind_, schema.nullable)

    def variable(self) -> _Node:
        kind, name = self.peek()
        if name == self.scope.row_var and self.peek(1) == ("op", "["):
            return self.row_field()
        if name == self.scope.guard_var and self.peek(1) != ("op", "["):
            self.take()
            return _Node(f"{self.scope.items_code}.length > 0", _TS["<"], "boolean")
        raise NotTranslatable()

    def truth(self, node: _Node) -> _Node:
        """`node` used as a truth value: allowed for booleans and numbers only."""
        if node.type not in ("boolean", "number"):
            raise NotTranslatable()
        return node

    def combine(self, op: str, left: _Node, right: _Node) -> _Node:
        if op in ("and", "or", "&&", "||"):
            ts_op = {"and": "&&", "or": "||"}.get(op, op)
            power = _TS[ts_op]
            left, right = self.truth(left), self.truth(right)
            # The right operand is parenthesised unless it binds tighter: this keeps PHP's grouping exactly.
            code = f"{self.wrap(left, power)} {ts_op} {self.wrap(right, power + 1)}"
            return _Node(code, power, "boolean")
        if left.type != right.type or left.type == "unknown":
            raise NotTranslatable("loose_compare_semantics" if {left.type, right.type} == {"string", "number"}
                                  else "condition_not_translatable")
        power = _TS[op]
        return _Node(f"{self.wrap(left, power)} {op} {self.wrap(right, power + 1)}", power, "boolean")

    @staticmethod
    def wrap(node: _Node, needed: int) -> str:
        return node.code if node.ts >= needed else f"({node.code})"


def translate(cond_expr: str | None, scope: Scope, context_reasons: dict[str, str]) -> Translation:
    """The TypeScript for one if/elseif condition, or the reason it is not translated."""
    if cond_expr is None:
        return Translation(None, "condition_unavailable")
    used = [(cond_expr.find(name), reason) for name, reason in context_reasons.items() if name in cond_expr]
    if used:
        return Translation(None, min(used)[1])
    try:
        parser = _Parser(_tokens(cond_expr), scope)
        node = parser.truth(parser.parse())
    except NotTranslatable as e:
        return Translation(None, e.reason)
    return Translation(node.code, None)
