<?php
declare(strict_types=1);

/* Q010 static subquery behavioral equivalence harness. */

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

/* SQL taken from legacy/10_subquery.php. */
$sql = "SELECT id, full_name FROM patients WHERE id IN (SELECT patient_id FROM appointments WHERE status = 'confirmed')";

$result = mysqli_query($conn, $sql);

if ($result === false) {
    fwrite(STDERR, "Q010 SQL execution failed.\n");
    exit(1);
}

$rows = [];

while ($row = $result->fetch_assoc()) {
    $rows[] = $row;
}

$result->free();
$conn->close();

echo json_encode($rows, JSON_THROW_ON_ERROR);
