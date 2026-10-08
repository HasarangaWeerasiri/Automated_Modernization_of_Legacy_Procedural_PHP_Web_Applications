
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
        choices=["run"],
        help="Run the migration pipeline",
    )
    parser.add_argument(
        "--php",
        required=True,
        help="Legacy PHP file or directory",
    )
    parser.add_argument(
        "--schema",
        required=True,
        help="MySQL schema SQL file",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output directory",
    )
    parser.add_argument(
        "--php-executable",
        default="php",
        help="PHP executable path or command",
    )

    args = parser.parse_args()

    summary = run_pipeline(
        php_path=args.php,
        schema_file=args.schema,
        output_dir=args.output,
        php_executable=args.php_executable,
    )

    print("\nMigration pipeline completed.")
    print(json.dumps(summary, indent=4))


if __name__ == "__main__":
    main()
