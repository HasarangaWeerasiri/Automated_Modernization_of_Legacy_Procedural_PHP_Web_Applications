# S2: no silent drops, audit FR1 probe before and after

Before = audit run (code @01869d8). After = this commit. 11 probe files, one call each. Command: `python -m c3.cli run --php evidence/soundness/S2_executors/probes --schema evidence/soundness/S2_executors/probes/schema.sql --output <tmp>`.

Rows produced: before **8/11**, after **11/11**.

| Probe | Before (executor / recovery / reason / conversion) | After (executor / recovery / reason / conversion) |
|---|---|---|
| `a_mysqli_proc.php` | mysqli_query / RECOVERED / None / CONVERTED | mysqli_query / RECOVERED / None / CONVERTED |
| `b_mysqli_oo.php` | query / RECOVERED / None / CONVERTED | query / RECOVERED / None / CONVERTED |
| `c_mysql_legacy.php` | mysql_query / RECOVERED / None / CONVERTED | mysql_query / RECOVERED / None / CONVERTED |
| `d_pdo_query.php` | query / RECOVERED / None / CONVERTED | query / RECOVERED / None / CONVERTED |
| `e_pdo_prepare_pos.php` | prepare / RECOVERED / None / UNRESOLVED | prepare / RECOVERED / None / UNRESOLVED |
| `f_pdo_prepare_named.php` | prepare / RECOVERED / None / UNRESOLVED | prepare / RECOVERED / None / UNRESOLVED |
| `g_mysqli_prepare_proc.php` | **no row (silently dropped)** | mysqli_prepare / RECOVERED / None / UNRESOLVED |
| `h_mysqli_prepare_oo.php` | prepare / RECOVERED / None / UNRESOLVED | prepare / RECOVERED / None / UNRESOLVED |
| `i_pdo_exec.php` | exec / RECOVERED / None / CONVERTED | exec / RECOVERED / None / CONVERTED |
| `j_mysqli_multi.php` | **no row (silently dropped)** | mysqli_multi_query / UNRESOLVED / MULTI_STATEMENT_EXECUTOR / UNRESOLVED |
| `k_mysqli_real.php` | **no row (silently dropped)** | mysqli_real_query / RECOVERED / None / CONVERTED |

Prepared statements (`?`, `:name`) are detected but still conversion-unresolved. Converting them is task S14.
