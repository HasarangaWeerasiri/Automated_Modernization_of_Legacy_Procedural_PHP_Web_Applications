<?php
error_reporting(E_ALL);

$legacyDir = realpath(
    __DIR__ . '/../../../../../benchmarks/apps/clinic_inventory_legacy_hard/legacy'
);

$queryId = $argv[1] ?? '';

switch ($queryId) {
    case 'Q003':
        $_POST['name'] = 'Dina';
        $_POST['email'] = 'dina@example.test';
        $_POST['department_id'] = '1';
        require $legacyDir . '/03_add_patient.php';
        break;

    case 'Q004':
        $_POST['id'] = '1';
        $_POST['phone'] = '0700000000';
        require $legacyDir . '/04_update_contact.php';
        break;

    case 'Q005':
        $_POST['id'] = '2';
        require $legacyDir . '/05_delete_payment.php';
        break;

    default:
        fwrite(STDERR, "Unsupported query ID\n");
        exit(1);
}

if ($result === false) {
    fwrite(STDERR, mysqli_error($conn) . "\n");
    exit(1);
}

echo json_encode([
    'query_id' => $queryId,
    'success' => true,
    'affected_rows' => mysqli_affected_rows($conn),
], JSON_THROW_ON_ERROR);

mysqli_close($conn);
