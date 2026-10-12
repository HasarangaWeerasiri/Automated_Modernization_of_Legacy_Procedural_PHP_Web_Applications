"""Diagnostic-only SQLAlchemy generation for conditional SELECTs."""

from c3.conversion.branch_ir import validate_conditional_select_ir


def generate_conditional_select_source(ir: dict) -> str:
    """Generate isolated SQLAlchemy code without promoting query status."""

    if not validate_conditional_select_ir(ir):
        raise ValueError("Unsupported conditional SELECT diagnostic IR.")

    if ir.get("query_id") != "Q014":
        raise ValueError("Unsupported conditional query identifier.")

    condition = ir["condition"]

    if condition.get("input_key") != "active":
        raise ValueError("Unsupported Q014 input condition.")

    if_sql = ir["branches"]["IF"]
    else_sql = ir["branches"]["ELSE"]

    lines = [
        '"""Generated diagnostic-only conditional SELECT."""',
        "",
        "from sqlalchemy import text",
        "from c3.conversion.branch_binding import resolve_isset_condition",
        "",
        "",
        "def select_doctors_q014(session, inputs):",
        '    """Execute Q014 using PHP isset semantics."""',
        f"    condition = {condition!r}",
        "    active_only = resolve_isset_condition(condition, inputs)",
        "",
        "    if active_only:",
        f"        sql = {if_sql!r}",
        "    else:",
        f"        sql = {else_sql!r}",
        "",
        "    return session.execute(text(sql)).mappings().all()",
        "",
    ]

    return "\n".join(lines)
