# S1: loop/switch soundness, audit probes before and after

Before = audit run on 2026-10-15 with code @01869d8. After = this commit. Same probe files (`probes/`). Command: `python -m c3.cli run --php evidence/soundness/S1_loop_switch/probes --schema evidence/soundness/S1_loop_switch/probes/schema.sql --output <tmp>`.

| Probe | Before: recovery / reason / conversion | Before: generated SQL | After: recovery / reason / conversion |
|---|---|---|---|
| `a_loop_value.php` | RECOVERED / None / CONVERTED | `SELECT id FROM users WHERE 1=0 OR name = :n` | UNRESOLVED / LOOP_OR_SWITCH_CONTROL_FLOW / UNRESOLVED |
| `b_if_noelse.php` | UNRESOLVED / BRANCHING / UNRESOLVED | `None` | UNRESOLVED / BRANCHING / UNRESOLVED |
| `c_if_append_value.php` | UNRESOLVED / BRANCHING / UNRESOLVED | `None` | UNRESOLVED / BRANCHING / UNRESOLVED |
| `d_while_append.php` | RECOVERED / None / CONVERTED | `DELETE FROM users WHERE 1=0 OR id = :i` | UNRESOLVED / LOOP_OR_SWITCH_CONTROL_FLOW / UNRESOLVED |
| `e_switch.php` | RECOVERED / None / CONVERTED | `SELECT id FROM teams` | UNRESOLVED / LOOP_OR_SWITCH_CONTROL_FLOW / UNRESOLVED |
