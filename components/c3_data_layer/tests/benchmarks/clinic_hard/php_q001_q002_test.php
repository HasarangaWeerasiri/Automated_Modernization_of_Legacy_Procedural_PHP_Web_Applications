<?php
error_reporting(E_ALL);

$legacyDir = realpath(
    __DIR__ . '/../../../../../benchmarks/apps/clinic_inventory_legacy_hard/legacy'
);

$queryId = $argv[1] ?? 'Q001';
$id = $argv[2] ?? '1';

if ($queryId === 'Q001') {
    require $legacyDir . '/01_list_patients.php';
} elseif ($queryId === 'Q002') {
    $_GET['id'] = $id;
    require $legacyDir . '/02_find_patient.php';
} else {
    fwrite(STDERR, "Unsupported query ID\n");
    exit(1);
}

if ($result === false) {
    fwrite(STDERR, mysqli_error($conn) . "\n");
    exit(1);
}

$rows = mysqli_fetch_all($result, MYSQLI_ASSOC);

echo json_encode($rows, JSON_THROW_ON_ERROR);
mysqli_close($conn);
