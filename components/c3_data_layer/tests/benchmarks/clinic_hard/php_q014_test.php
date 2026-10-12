<?php
declare(strict_types=1);

/* Q014 PHP behavioral equivalence harness. */

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

$mode = $argv[1] ?? 'missing';
$value = $argv[2] ?? '';

$_GET = [];

if ($mode === 'null') {
    $_GET['active'] = null;
} elseif ($mode === 'value') {
    $_GET['active'] = $value;
} elseif ($mode !== 'missing') {
    fwrite(STDERR, "Unsupported test mode.\n");
    exit(1);
}

/* Original Q014 branch-selection logic. */

$activeOnly = isset($_GET['active']);

if ($activeOnly) {
    $sql = "SELECT id, full_name FROM doctors WHERE active = 1";
} else {
    $sql = "SELECT id, full_name FROM doctors";
}

$result = mysqli_query($conn, $sql);

if ($result === false) {
    fwrite(STDERR, "Q014 SQL execution failed.\n");
    exit(1);
}

$rows = [];

while ($row = $result->fetch_assoc()) {
    $rows[] = $row;
}

$result->free();
$conn->close();

echo json_encode($rows, JSON_THROW_ON_ERROR);
