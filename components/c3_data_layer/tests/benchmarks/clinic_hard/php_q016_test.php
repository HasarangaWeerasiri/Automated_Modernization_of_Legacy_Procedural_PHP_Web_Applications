<?php
declare(strict_types=1);

/* Q016: PHP baseline for conditional SQL concatenation. */

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
    $_GET['department_id'] = null;
} elseif ($mode === 'value') {
    $_GET['department_id'] = $value;
} elseif ($mode !== 'missing') {
    fwrite(STDERR, "Invalid test mode.\n");
    exit(1);
}

/* Reproduce the original Q016 query construction. */

$sql = "SELECT id, full_name FROM patients WHERE 1=1";

if (isset($_GET['department_id'])) {
    $sql .= " AND department_id = " . $_GET['department_id'];
}

$result = mysqli_query($conn, $sql);

if ($result === false) {
    fwrite(STDERR, "Q016 query execution failed.\n");
    exit(1);
}

$rows = [];

while ($row = $result->fetch_assoc()) {
    $rows[] = $row;
}

$result->free();
$conn->close();

echo json_encode($rows, JSON_THROW_ON_ERROR);
