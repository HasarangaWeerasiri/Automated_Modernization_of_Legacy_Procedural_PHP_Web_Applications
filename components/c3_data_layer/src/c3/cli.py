
"""Command-line interface for Data Layer Migration."""

import argparse
import json

from c3.pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Legacy PHP Data Layer Migration Tool"
    )

    parser.add_argument(
        "command",
        choices=["run", "evaluate"],
        help="run: migration pipeline; evaluate: compare an output folder with a site-level ground truth",
    )
    parser.add_argument(
        "--php",
        required=True,
        help="Legacy PHP file or directory",
    )
    parser.add_argument(
        "--schema",
        help="MySQL schema SQL file (run)",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output directory (run: written; evaluate: read)",
    )
    parser.add_argument(
        "--ground-truth",
        help="evaluate: ground truth queries.json (site level)",
    )
    parser.add_argument(
        "--report",
        help="evaluate: where to write the evaluation JSON",
    )
    parser.add_argument(
        "--php-executable",
        default="php",
        help="PHP executable path or command",
    )

    args = parser.parse_args()

    if args.command == "evaluate":
        from c3.evaluation.site_evaluator import evaluate_output

        if not args.ground_truth:
            parser.error("evaluate needs --ground-truth")
        result = evaluate_output(args.ground_truth, args.output, args.php, args.report)
        print(json.dumps({k: result[k] for k in ("gt_live_sites", "detection", "recovery", "column_use_accuracy")}, indent=4, default=str))
        return

    if not args.schema:
        parser.error("run needs --schema")

    summary = run_pipeline(
        php_path=args.php,
        schema_file=args.schema,
        output_dir=args.output,
        php_executable=args.php_executable,
    )

    print("\nMigration pipeline completed.")
    print("Primary outputs: " + ", ".join(summary["primary_outputs"]))
    print("Supporting artifacts: " + ", ".join(summary["supporting_artifacts"]))
    print(json.dumps(summary, indent=4))


if __name__ == "__main__":
    main()
