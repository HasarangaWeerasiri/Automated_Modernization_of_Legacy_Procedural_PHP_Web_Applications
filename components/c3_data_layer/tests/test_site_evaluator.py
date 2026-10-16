"""S15 site-level evaluator: synthetic ground truth (written for this test)."""

from c3.evaluation.site_evaluator import evaluate_sites

SCHEMA = {"tables": [{"name": "t", "columns": [{"name": "id"}, {"name": "a"}]}]}


def gt(file, line, recoverable, status="live", **expected):
    entry = {"query_id": f"G{line}", "source_file": file, "line": line, "site_status": status,
             "expected_recoverable": recoverable, "expected_operation": "SELECT", "expected_tables": ["t"],
             "expected_read_columns": ["a"], "expected_write_columns": [], "expected_filter_columns": ["id"]}
    entry.update(expected)
    return entry


def c3(query_id, file, line, status):
    return {"query_id": query_id, "source_file": f"/app/{file}", "source_line": line, "status": status}


def usage(query_id, read=("a",), tables=("t",)):
    return {"query_id": query_id, "operation": "SELECT", "tables": list(tables), "read_columns": list(read),
            "write_columns": [], "filter_columns": ["ID"]}


def test_outcomes_ambiguous_commented_and_schema_mismatch():
    ground_truth = [
        gt("a.php", 1, True), gt("a.php", 2, True), gt("a.php", 3, False), gt("a.php", 4, False),
        gt("a.php", 5, "ambiguous"), gt("a.php", 6, False, status="commented_out"),
        gt("a.php", 7, True, expected_write_columns=["payment"], expected_read_columns=[]),
    ]
    queries = [c3("Q1", "a.php", 1, "RECOVERED"), c3("Q2", "a.php", 2, "UNRESOLVED"),
               c3("Q3", "a.php", 3, "RECOVERED"), c3("Q4", "a.php", 4, "UNRESOLVED"),
               c3("Q5", "a.php", 5, "RECOVERED"), c3("Q6", "a.php", 6, "RECOVERED"),
               c3("Q7", "a.php", 7, "RECOVERED"), c3("Q8", "b.php", 9, "RECOVERED")]
    column_usage = [usage("Q1"), usage("Q3"), usage("Q7", read=())]

    result = evaluate_sites(ground_truth, queries, column_usage, SCHEMA, "/app")

    r = result["recovery"]
    assert (r["TP"], r["FN"], r["FP"], r["TN"], r["excluded_ambiguous"]) == (2, 1, 1, 1, 1)
    assert r["precision"] == 66.7 and r["recall"] == 66.7
    assert r["false_recoveries"] == ["a.php:3"] and r["false_abstentions"] == ["a.php:2"]
    assert [(d["site"], d["site_status"]) for d in result["detection"]["non_live_sites_reported"]] == [("a.php:6", "commented_out")]
    assert result["detection"]["c3_sites_not_in_gt"] == [{"site": "b.php:9", "c3_query_id": "Q8"}]
    assert result["column_use_accuracy"]["sites"] == 1 and result["column_use_accuracy"]["read"] == 100.0
    assert result["column_use_accuracy"]["filter"] == 100.0  # case-insensitive
    assert result["schema_mismatch"]["sites"] == [{"site": "a.php:7", "problems": ["column payment not in ['t']"]}]


def test_column_difference_is_reported():
    result = evaluate_sites([gt("a.php", 1, True)], [c3("Q1", "a.php", 1, "RECOVERED")],
                            [usage("Q1", read=("id",))], SCHEMA, "/app")

    assert result["column_use_accuracy"]["read"] == 0.0
    assert result["details"][0]["column_differences"]["c3"]["read_columns"] == ["id"]
