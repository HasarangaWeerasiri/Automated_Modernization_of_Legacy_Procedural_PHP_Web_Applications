
<?php
/**
 * C3 Data Layer Migration
 * Hard CRUD - Legacy PHP LIKE Search Validation
 *
 * Executes the original PHP benchmark file.
 * Does not modify the legacy source code.
 */

mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);

$password = getenv("C3_DB_PASSWORD");

if ($password === false || $password === "") {
    fwrite(STDERR, "C3_DB_PASSWORD is not set.\n");
    exit(1);
}

$legacyFile = dirname(__DIR__, 4)
    . "/benchmarks/apps/hard_crud/legacy/01_like_search.php";

$outputFile = __DIR__ . "/hard_legacy_results.json";

$searchTerms = [
    "Ali",
    "Alex",
    "Bob",
    "Charlie",
    "xyz"
];

function normalizeUsers($rows) {
    $normalized = [];

    foreach ($rows as $row) {
        $normalized[] = [
            "id" => (int)$row["id"],
            "name" => $row["name"],
            "email" => $row["email"]
        ];
    }

    usort($normalized, function ($a, $b) {
        return $a["id"] <=> $b["id"];
    });

    return $normalized;
}

try {
    $conn = new mysqli(
        "localhost",
        "c3_tester",
        $password,
        "c3_hard_php"
    );

    $conn->set_charset("utf8mb4");

    echo "Connected to c3_hard_php successfully.\n";

    $count = (int)$conn->query(
        "SELECT COUNT(*) AS total FROM users"
    )->fetch_assoc()["total"];

    if ($count !== 5) {
        throw new RuntimeException(
            "Expected exactly 5 test users."
        );
    }

    $results = [];

    foreach ($searchTerms as $term) {
        echo "\nSearching for: $term\n";

        $_GET = ["q" => $term];

        // Execute the original legacy PHP query.
        require $legacyFile;

        $rows = normalizeUsers(
            $result->fetch_all(MYSQLI_ASSOC)
        );

        $results[$term] = $rows;

        echo json_encode(
            $rows,
            JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR
        ) . "\n";
    }

    $report = [
        "implementation" => "legacy_php",
        "benchmark" => "hard_crud",
        "query_id" => "Q001",
        "search_results" => $results
    ];

    file_put_contents(
        $outputFile,
        json_encode(
            $report,
            JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR
        )
    );

    echo "\nResults saved to: $outputFile\n";
    echo "Hard CRUD legacy PHP validation completed.\n";

    $conn->close();

} catch (Throwable $e) {
    fwrite(
        STDERR,
        "Validation failed: " . $e->getMessage() . "\n"
    );
    exit(1);
}
