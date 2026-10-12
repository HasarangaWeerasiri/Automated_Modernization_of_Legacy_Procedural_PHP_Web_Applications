"""Generated diagnostic-only conditional SELECT."""

from sqlalchemy import text
from c3.conversion.branch_binding import resolve_isset_condition


def select_doctors_q014(session, inputs):
    """Execute Q014 using PHP isset semantics."""
    condition = {'condition_variable': 'activeOnly', 'kind': 'ISSET', 'input_source': '_GET', 'input_key': 'active', 'true_when': 'present_and_not_null'}
    active_only = resolve_isset_condition(condition, inputs)

    if active_only:
        sql = 'SELECT id, full_name FROM doctors WHERE active = 1'
    else:
        sql = 'SELECT id, full_name FROM doctors'

    return session.execute(text(sql)).mappings().all()
