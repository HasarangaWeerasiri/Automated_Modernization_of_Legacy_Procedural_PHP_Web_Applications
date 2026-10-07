import json
from pathlib import Path

from c3.analysis.column_analyzer import analyze_query_records


def generate_column_usage(
    queries_file: Path,
    output_file: Path,
) -> list[dict]:
    """
    Read recovered queries from queries.json, perform column-use
    analysis, and write the results to column_usage.json.
    """

    with queries_file.open(
        "r",
        encoding="utf-8",
    ) as file:
        queries = json.load(file)

    results = analyze_query_records(queries)

    output = [
        result.to_dict()
        for result in results
    ]

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_file.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            output,
            file,
            indent=4,
            ensure_ascii=False,
        )

    return output


def main() -> None:
    """
    Generate column usage for the simple_crud benchmark.
    """

    component_root = Path(__file__).resolve().parents[3]
    repository_root = component_root.parents[1]

    queries_file = (
        repository_root
        / "output"
        / "simple_crud"
        / "queries.json"
    )

    output_file = (
        repository_root
        / "output"
        / "simple_crud"
        / "column_usage.json"
    )

    if not queries_file.exists():
        raise FileNotFoundError(
            f"Queries file not found: {queries_file}"
        )

    results = generate_column_usage(
        queries_file,
        output_file,
    )

    analyzed = sum(
        1
        for result in results
        if result["status"] == "ANALYZED"
    )

    unresolved = sum(
        1
        for result in results
        if result["status"] == "UNRESOLVED"
    )

    print(
        f"Column usage generated: {output_file}"
    )
    print(f"Total queries: {len(results)}")
    print(f"Analyzed: {analyzed}")
    print(f"Unresolved: {unresolved}")


if __name__ == "__main__":
    main()