<?php
error_reporting(E_ALL);

$legacyDir = realpath(
    __DIR__ . '/../../../../../benchmarks/apps/clinic_inventory_legacy_hard/legacy'
);

$queryId = $argv[1] ?? '';
$q = $argv[2] ?? '';

switch ($queryId) {
    case 'Q006':
        $_GET['q'] = $q;
        require $legacyDir . '/06_search_like.php';
        break;

    case 'Q007':
        require $legacyDir . '/07_literal_predicate.php';
        break;

    case 'Q009':
        require $legacyDir . '/09_group_totals.php';
        break;

    default:
        fwrite(STDERR, "Unsupported query ID\n");
        exit(1);
}

if ($result === false) {
    fwrite(STDERR, mysqli_error($conn) . "\n");
    exit(1);
}

$rows = mysqli_fetch_all($result, MYSQLI_ASSOC);

echo json_encode(
    $rows,
    JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE
);

mysqli_close($conn);
