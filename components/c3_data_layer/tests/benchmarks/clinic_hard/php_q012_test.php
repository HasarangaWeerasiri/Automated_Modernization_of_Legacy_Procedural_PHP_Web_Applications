<?php
declare(strict_types=1);

/* Q012 legacy ORDER BY comparison. */

$host = getenv('C3_BENCH_DB_HOST') ?: '127.0.0.1';
$user = getenv('C3_BENCH_DB_USER') ?: 'c3_tester';
$password = getenv('C3_BENCH_DB_PASSWORD');
$database = getenv('C3_BENCH_DB_NAME') ?: 'c3_clinic_legacy_test';

if ($password === false) {
    fwrite(STDERR, "Database password not configured.\n");
    exit(1);
}

$conn = new mysqli($host, $user, $password, $database);

if ($conn->connect_errno) {
    fwrite(STDERR, "Database connection failed.\n");
    exit(1);
}

$conn->set_charset('utf8mb4');

$mode = $argv[1] ?? 'missing';
$value = $argv[2] ?? '';

$_GET = [];

if ($mode === 'null') {
    $_GET['sort'] = null;
} elseif ($mode === 'value') {
    $_GET['sort'] = $value;
} elseif ($mode !== 'missing') {
    fwrite(STDERR, "Invalid test mode.\n");
    exit(1);
}

/* Reproduce Q012's original SQL construction. */
$sort = $_GET['sort'] ?? 'full_name';

$result = mysqli_query(
    $conn,
    "SELECT id, full_name FROM patients ORDER BY $sort"
);

if ($result === false) {
    fwrite(STDERR, "Q012 SQL execution failed.\n");
    exit(1);
}

$rows = [];

while ($row = $result->fetch_assoc()) {
    $rows[] = $row;
}

$result->free();
$conn->close();

echo json_encode($rows, JSON_THROW_ON_ERROR);
