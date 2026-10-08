
<?php
/**
 * C3 Data Layer Migration
 * Mixed CRUD - Legacy PHP MySQL Validation
 *
 * Executes original PHP benchmark files without modifying them.
 */

mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);

$password = getenv("C3_DB_PASSWORD");

if ($password === false || $password === "") {
    fwrite(STDERR, "Error: C3_DB_PASSWORD is not set.\n");
    exit(1);
}

$legacyPath = dirname(__DIR__, 4)
    . "/benchmarks/apps/mixed_crud/legacy/";

$outputFile = __DIR__ . "/mixed_legacy_results.json";

function normalizeProducts($rows) {
    return array_map(function ($row) {
        return [
            "id" => (int)$row["id"],
            "title" => $row["title"],
            "price" => number_format((float)$row["price"], 2, ".", "")
        ];
    }, $rows);
}

try {
    $conn = new mysqli(
        "localhost",
        "c3_tester",
        $password,
        "c3_mixed_php"
    );

    $conn->set_charset("utf8mb4");

    echo "Connected to c3_mixed_php successfully.\n";

    // Verify the controlled starting dataset.
    $initialRows = $conn->query(
        "SELECT id, title, price FROM products ORDER BY id"
    )->fetch_all(MYSQLI_ASSOC);

    $expectedInitial = [
        ["id" => 1, "title" => "Laptop", "price" => "150000.00"],
        ["id" => 2, "title" => "Mouse", "price" => "2500.00"],
        ["id" => 3, "title" => "Keyboard", "price" => "7500.00"]
    ];

    if (normalizeProducts($initialRows) !== $expectedInitial) {
        throw new RuntimeException(
            "Unexpected initial data. Database was not modified."
        );
    }

    echo "Initial dataset verified: 3 products.\n";

    // Q001 - SELECT all products.
    echo "\nQ001 - SELECT ALL\n";

    require $legacyPath . "01_select.php";

    $q001 = normalizeProducts(
        $result->fetch_all(MYSQLI_ASSOC)
    );

    echo json_encode($q001, JSON_PRETTY_PRINT) . "\n";

    // Q002 - SELECT product by ID.
    echo "\nQ002 - SELECT BY ID\n";

    $_GET = ["id" => 1];

    require $legacyPath . "02_interpolation.php";

    $q002 = normalizeProducts(
        $result->fetch_all(MYSQLI_ASSOC)
    );

    if (count($q002) !== 1 || $q002[0]["title"] !== "Laptop") {
        throw new RuntimeException("Q002 validation failed.");
    }

    echo json_encode($q002, JSON_PRETTY_PRINT) . "\n";

    // Q004 - SELECT after variable reassignment.
    echo "\nQ004 - REASSIGNMENT SELECT\n";

    require $legacyPath . "04_reassignment.php";

    // The original file does not retain the query result.
    // Verify the database state after execution.
    $q004 = normalizeProducts(
        $conn->query(
            "SELECT id, title, price FROM products ORDER BY id"
        )->fetch_all(MYSQLI_ASSOC)
    );

    if ($q004 !== $expectedInitial) {
        throw new RuntimeException("Q004 state validation failed.");
    }

    echo "Q004 executed successfully.\n";

    // Q005 - UPDATE product.
    echo "\nQ005 - UPDATE PRODUCT\n";

    $_POST = [
        "id" => 1,
        "title" => "Laptop Updated",
        "price" => "155000.00"
    ];

    require $legacyPath . "05_update.php";

    $updated = $conn->query(
        "SELECT id, title, price FROM products WHERE id = 1"
    )->fetch_assoc();

    if (!$updated) {
        throw new RuntimeException("Q005: Product not found.");
    }

    $q005 = normalizeProducts([$updated])[0];

    if (
        $q005["title"] !== "Laptop Updated" ||
        $q005["price"] !== "155000.00"
    ) {
        throw new RuntimeException("Q005 validation failed.");
    }

    echo json_encode($q005, JSON_PRETTY_PRINT) . "\n";

    // Q003 - DELETE product.
    echo "\nQ003 - DELETE PRODUCT\n";

    $_GET = ["id" => 2];

    require $legacyPath . "03_concat.php";

    $deletedCount = (int)$conn->query(
        "SELECT COUNT(*) AS total FROM products WHERE id = 2"
    )->fetch_assoc()["total"];

    if ($deletedCount !== 0) {
        throw new RuntimeException("Q003 validation failed.");
    }

    echo "Mouse deleted successfully.\n";

    // Final database state.
    echo "\nFINAL DATABASE STATE\n";

    $finalRows = normalizeProducts(
        $conn->query(
            "SELECT id, title, price FROM products ORDER BY id"
        )->fetch_all(MYSQLI_ASSOC)
    );

    echo json_encode($finalRows, JSON_PRETTY_PRINT) . "\n";

    $validationResults = [
        "implementation" => "legacy_php",
        "benchmark" => "mixed_crud",
        "q001" => $q001,
        "q002" => $q002,
        "q004_state" => $q004,
        "q005" => $q005,
        "q003_deleted" => $deletedCount === 0,
        "final_state" => $finalRows,
        "q006" => "manual_review_required"
    ];

    file_put_contents(
        $outputFile,
        json_encode(
            $validationResults,
            JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR
        )
    );

    echo "\nResults saved to: $outputFile\n";
    echo "Mixed CRUD legacy PHP validation completed.\n";

    $conn->close();

} catch (Throwable $e) {
    fwrite(
        STDERR,
        "Validation failed: " . $e->getMessage() . "\n"
    );
    exit(1);
}
