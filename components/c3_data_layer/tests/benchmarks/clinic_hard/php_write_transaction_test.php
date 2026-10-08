<?php
error_reporting(E_ALL);

$legacyDir = realpath(
    __DIR__ . '/../../../../../benchmarks/apps/clinic_inventory_legacy_hard/legacy'
);

if (!$legacyDir) {
    fwrite(STDERR, "Legacy directory not found\n");
    exit(1);
}

$queryId = $argv[1] ?? '';

$allowed = ['Q003', 'Q004', 'Q005', 'Q017'];

if (!in_array($queryId, $allowed, true)) {
    fwrite(STDERR, "Unsupported query ID\n");
    exit(1);
}

// Load the exact connection used by the original PHP scripts.
require_once $legacyDir . '/db.php';

mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);

mysqli_begin_transaction($conn);

try {
    switch ($queryId) {
        case 'Q003':
            $_POST['name'] = 'C3 Transaction Patient';
            $_POST['email'] = 'c3-transaction@example.test';
            $_POST['department_id'] = '1';
            require $legacyDir . '/03_add_patient.php';

            $checkSql = "
                SELECT department_id, full_name, email, created_at
                FROM patients
                WHERE email = 'c3-transaction@example.test'
            ";
            break;

        case 'Q004':
            $_POST['id'] = '1';
            $_POST['phone'] = '0799999999';
            require $legacyDir . '/04_update_contact.php';

            $checkSql = "
                SELECT id, phone
                FROM patients
                WHERE id = 1
            ";
            break;

        case 'Q005':
            $_POST['id'] = '1';
            require $legacyDir . '/05_delete_payment.php';

            $checkSql = "
                SELECT id, appointment_id, amount
                FROM payments
                ORDER BY id
            ";
            break;

        case 'Q017':
            $_POST['id'] = '1';
            $_POST['delta'] = '7.25';
            require $legacyDir . '/17_update_arithmetic.php';

            $checkSql = "
                SELECT id, fee
                FROM appointments
                ORDER BY id
            ";
            break;
    }

    if ($result === false) {
        throw new RuntimeException(mysqli_error($conn));
    }

    $affectedRows = mysqli_affected_rows($conn);

    $checkResult = mysqli_query($conn, $checkSql);
    $rows = mysqli_fetch_all($checkResult, MYSQLI_ASSOC);

    echo json_encode([
        'query_id' => $queryId,
        'affected_rows' => $affectedRows,
        'rows' => $rows,
    ], JSON_THROW_ON_ERROR);

} finally {
    // Undo all writes from this test.
    mysqli_rollback($conn);
    mysqli_close($conn);
}