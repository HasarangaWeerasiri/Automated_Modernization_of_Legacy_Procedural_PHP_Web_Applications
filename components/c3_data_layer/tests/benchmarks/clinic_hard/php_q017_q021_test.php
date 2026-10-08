<?php
error_reporting(E_ALL);

$legacyDir = realpath(
    __DIR__ . '/../../../../../benchmarks/apps/clinic_inventory_legacy_hard/legacy'
);

$queryId = $argv[1] ?? '';

switch ($queryId) {
    case 'Q017':
        $_POST['id'] = $argv[2] ?? '1';
        $_POST['delta'] = $argv[3] ?? '5.00';
        require $legacyDir . '/17_update_arithmetic.php';
        break;

    case 'Q018':
        $_GET['minimum'] = $argv[2] ?? '100';
        require $legacyDir . '/18_multi_condition.php';
        break;

    case 'Q019':
        $_GET['limit'] = $argv[2] ?? '2';
        $_GET['offset'] = $argv[3] ?? '0';
        require $legacyDir . '/19_limit_offset.php';
        break;

    case 'Q021':
        require $legacyDir . '/21_quoted_literal.php';
        break;

    default:
        fwrite(STDERR, "Unsupported query ID\n");
        exit(1);
}

if ($result === false) {
    fwrite(STDERR, mysqli_error($conn) . "\n");
    exit(1);
}

if ($result === true) {
    echo json_encode([
        'affected_rows' => mysqli_affected_rows($conn)
    ], JSON_THROW_ON_ERROR);
} else {
    echo json_encode(
        mysqli_fetch_all($result, MYSQLI_ASSOC),
        JSON_THROW_ON_ERROR
    );
}

mysqli_close($conn);
