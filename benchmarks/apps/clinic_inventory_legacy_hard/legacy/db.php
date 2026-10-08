<?php
// Procedural mysqli bootstrap. Configure using environment variables.
$host = getenv('C3_BENCH_DB_HOST') ?: '127.0.0.1';
$user = getenv('C3_BENCH_DB_USER') ?: 'c3_tester';
$pass = getenv('C3_BENCH_DB_PASSWORD');
$dbname = getenv('C3_BENCH_DB_NAME') ?: 'c3_clinic_legacy_hard';
if ($pass === false) { die('Set C3_BENCH_DB_PASSWORD before running.'); }
$conn = mysqli_connect($host, $user, $pass, $dbname);
if (!$conn) { die('Could not connect to isolated benchmark database.'); }
mysqli_set_charset($conn, 'utf8mb4');
