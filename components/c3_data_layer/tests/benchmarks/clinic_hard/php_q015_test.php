<?php
declare(strict_types=1);

$legacyDir = realpath(
    __DIR__ . '/../../../../../benchmarks/apps/clinic_inventory_legacy_hard/legacy'
);

if ($legacyDir === false) {
    fwrite(STDERR, "Legacy directory not found.\n");
    exit(1);
}

require_once $legacyDir . '/query_factory.php';

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

$departmentId = $argv[1] ?? '1';

/*
 * Use the original legacy PHP function.
 * The benchmark passes only validated numeric identifiers.
 */
$sql = makeDoctorQuery($departmentId);

$result = $conn->query($sql);

if ($result === false) {
    fwrite(STDERR, "Legacy SQL execution failed.\n");
    exit(1);
}

$rows = [];

while ($row = $result->fetch_assoc()) {
    $rows[] = $row;
}

$result->free();
$conn->close();

echo json_encode($rows, JSON_THROW_ON_ERROR);
