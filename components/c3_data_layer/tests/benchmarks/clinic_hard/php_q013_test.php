<?php
declare(strict_types=1);

/* Q013 PHP behavior for the supported patients table. */

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
    $_GET['table'] = null;
} elseif ($mode === 'value') {
    $_GET['table'] = $value;
} elseif ($mode !== 'missing') {
    fwrite(STDERR, "Invalid test mode.\n");
    exit(1);
}

/* Reproduce the legacy Q013 query construction. */

$table = $_GET['table'] ?? 'patients';

$result = mysqli_query(
    $conn,
    "SELECT id FROM $table WHERE id = 1"
);

if ($result === false) {
    fwrite(STDERR, "Q013 query execution failed.\n");
    exit(1);
}

$rows = [];

while ($row = $result->fetch_assoc()) {
    $rows[] = $row;
}

$result->free();
$conn->close();

echo json_encode($rows, JSON_THROW_ON_ERROR);
