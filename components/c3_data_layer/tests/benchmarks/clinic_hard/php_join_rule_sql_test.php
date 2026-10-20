<?php
// Direct-SQL side of test_join_rule_mysql.py (FR6 join rule). Not the original application PHP:
// it builds the SQL the way legacy code does (raw string interpolation of each {{name}}), runs it
// with mysqli_query and reads rows with mysqli_fetch_assoc, so row keys follow PHP semantics.
// usage: php php_join_rule_sql_test.php '<sql template>' '<json object of values>'
error_reporting(E_ALL);
mysqli_report(MYSQLI_REPORT_OFF);

$template = $argv[1] ?? '';
$values = json_decode($argv[2] ?? '{}', true, 512, JSON_THROW_ON_ERROR);

$sql = preg_replace_callback('/\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}/', function ($m) use ($values) {
    if (!array_key_exists($m[1], $values)) {
        fwrite(STDERR, "Missing value for {$m[1]}\n");
        exit(1);
    }
    return (string) $values[$m[1]];
}, $template);

$conn = mysqli_connect(getenv('C3_BENCH_DB_HOST') ?: '127.0.0.1', getenv('C3_BENCH_DB_USER') ?: 'c3_tester',
    getenv('C3_BENCH_DB_PASSWORD'), getenv('C3_BENCH_DB_NAME'));
if (!$conn) {
    fwrite(STDERR, "Could not connect\n");
    exit(1);
}
mysqli_set_charset($conn, 'utf8mb4');

$result = mysqli_query($conn, $sql);
if ($result === false) {
    fwrite(STDERR, mysqli_error($conn) . "\n");
    exit(1);
}

$rows = [];
while ($row = mysqli_fetch_assoc($result)) {
    $rows[] = $row;
}

echo json_encode($rows, JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE);
mysqli_close($conn);
