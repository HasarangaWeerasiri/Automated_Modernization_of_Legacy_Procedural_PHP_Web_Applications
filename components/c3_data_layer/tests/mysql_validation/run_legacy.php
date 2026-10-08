
<?php
/**
 * C3 Data Layer Migration
 * Legacy PHP Behavioral Validation
 *
 * Executes the original PHP benchmark files
 * against an isolated MySQL test database.
 */

mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);

$host = "localhost";
$database = "c3_php_validation";
$username = "c3_tester";

// Read the password from an environment variable.
$password = getenv("C3_DB_PASSWORD");

if ($password === false || $password === "") {
    fwrite(STDERR, "Error: C3_DB_PASSWORD is not set.\n");
    exit(1);
}

$legacyPath = dirname(__DIR__, 4)
    . "/benchmarks/apps/simple_crud/legacy/";

try {
    $conn = new mysqli($host, $username, $password, $database);
    $conn->set_charset("utf8mb4");

    echo "Connected to MySQL successfully.\n";

    // Initial database state
    $result = $conn->query("SELECT COUNT(*) AS total FROM users");
    $row = $result->fetch_assoc();

    if ((int)$row["total"] !== 0) {
        throw new RuntimeException(
            "Validation database is not empty. Aborting to protect existing data."
        );
    }

    echo "\n1. CREATE USER\n";

    $_POST = [
        "name" => "Alice",
        "email" => "alice@example.com"
    ];

    require $legacyPath . "create_user.php";

    $userId = $conn->insert_id;
    echo "Created user ID: $userId\n";

$createdResult = $conn->query(
    "SELECT id, name, email, status FROM users WHERE id = " . (int)$userId
);

$createdUser = $createdResult->fetch_assoc();

if (
    !$createdUser ||
    $createdUser["name"] !== "Alice" ||
    $createdUser["email"] !== "alice@example.com" ||
    $createdUser["status"] !== "active"
) {
    throw new RuntimeException("CREATE validation failed.");
}

echo "CREATE verified against MySQL.\n";


    echo "\n2. READ USERS\n";

    require $legacyPath . "users.php";

    $users = $conn->query(
        "SELECT id, name, email, status FROM users ORDER BY id"
    );

    while ($user = $users->fetch_assoc()) {
        echo json_encode($user) . "\n";
    }

    echo "\n3. UPDATE USER\n";

    $_POST = [
        "id" => $userId,
        "name" => "Alice Updated"
    ];

    require $legacyPath . "update_user.php";

    $updatedResult = $conn->query(
        "SELECT id, name, email, status FROM users WHERE id = " . (int)$userId
    );

    $updatedUser = $updatedResult->fetch_assoc();

    if (!$updatedUser || $updatedUser["name"] !== "Alice Updated") {
        throw new RuntimeException("UPDATE validation failed.");
    }

    echo "Updated user ID: $userId\n";
    echo json_encode($updatedUser) . "\n";

    echo "\n4. DELETE USER\n";

    $_GET = [
        "id" => $userId
    ];

    require $legacyPath . "delete_user.php";

    echo "Deleted user ID: $userId\n";

    echo "\n5. FINAL DATABASE STATE\n";

    $users = $conn->query(
        "SELECT id, name, email, status FROM users ORDER BY id"
    );

    $finalRows = $users->fetch_all(MYSQLI_ASSOC);

    echo json_encode(
        $finalRows,
        JSON_PRETTY_PRINT
    ) . "\n";

    $validationResults = [
        "implementation" => "legacy_php",

"create" => [
    "name" => $createdUser["name"],
    "email" => $createdUser["email"],
    "status" => $createdUser["status"]
],

        "read" => [
            "name" => "Alice",
            "email" => "alice@example.com",
            "status" => "active"
        ],
        "update" => [
            "name" => $updatedUser["name"],
            "email" => $updatedUser["email"],
            "status" => $updatedUser["status"]
        ],
        "delete" => count($finalRows) === 0,
        "final_state" => $finalRows
];

$outputFile = __DIR__ . "/legacy_results.json";

file_put_contents(
    $outputFile,
    json_encode($validationResults, JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR)
);

echo "\nValidation results saved to: $outputFile\n";
echo "Legacy PHP CRUD execution completed.\n";

$conn->close();

} catch (Throwable $e) {
    fwrite(STDERR, "Validation failed: " . $e->getMessage() . "\n");
    exit(1);
}
