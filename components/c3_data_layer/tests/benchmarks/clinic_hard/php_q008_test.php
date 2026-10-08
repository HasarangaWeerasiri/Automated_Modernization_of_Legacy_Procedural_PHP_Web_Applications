<?php
error_reporting(E_ALL);

$legacyDir = realpath(
    __DIR__ . '/../../../../../benchmarks/apps/clinic_inventory_legacy_hard/legacy'
);

$_GET['patient_id'] = $argv[1] ?? '1';

require $legacyDir . '/08_join_appointments.php';

if ($result === false) {
    fwrite(STDERR, mysqli_error($conn) . "\n");
    exit(1);
}

echo json_encode(
    mysqli_fetch_all($result, MYSQLI_ASSOC),
    JSON_THROW_ON_ERROR
);

mysqli_close($conn);