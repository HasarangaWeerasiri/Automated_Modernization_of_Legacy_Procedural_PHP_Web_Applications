<?php
declare(strict_types=1);

mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);

$password = getenv('C3_BENCH_DB_PASSWORD');

if ($password === false) {
    fwrite(STDERR, "Database password is not configured.\n");
    exit(1);
}

$conn = new mysqli(
    '127.0.0.1',
    'c3_tester',
    $password,
    'c3_clinic_legacy_test'
);

$conn->set_charset('utf8mb4');

$id = $argv[1] ?? '1';

if (!preg_match('/^[1-9][0-9]*$/D', $id)
    || strlen($id) > 10
    || (float)$id > 2147483647) {
    fwrite(STDERR, "Invalid appointment ID.\n");
    exit(1);
}

$conn->begin_transaction();

try {
    $lookup = $conn->prepare(
        "SELECT a.fee, d.hourly_rate
         FROM appointments a
         JOIN doctors d ON d.id = a.doctor_id
         WHERE a.id = ?"
    );
    $lookup->bind_param('i', $id);
    $lookup->execute();
    $before = $lookup->get_result()->fetch_assoc();
    $lookup->close();

    if ($before === null) {
        throw new RuntimeException("Appointment not found.");
    }

    $originalFee = $before['fee'];
    $doctorRate = $before['hourly_rate'];

    $temporaryFee = number_format(
        (float)$doctorRate + 1.00,
        2,
        '.',
        ''
    );

    $seed = $conn->prepare(
        "UPDATE appointments SET fee = ? WHERE id = ?"
    );
    $seed->bind_param('si', $temporaryFee, $id);
    $seed->execute();
    $seed->close();

    /* Original Q020 statement, preserved for comparison. */
    $sql = "UPDATE appointments a JOIN doctors d ON d.id = a.doctor_id SET a.fee = d.hourly_rate WHERE a.id = $id";
    $result = mysqli_query($conn, $sql);

    $affected = $conn->affected_rows;

    $verify = $conn->prepare(
        "SELECT fee FROM appointments WHERE id = ?"
    );
    $verify->bind_param('i', $id);
    $verify->execute();
    $after = $verify->get_result()->fetch_assoc();
    $verify->close();

    $output = [
        'appointment_id' => (int)$id,
        'original_fee' => $originalFee,
        'doctor_rate' => $doctorRate,
        'updated_fee' => $after['fee'],
        'affected_rows' => $affected,
    ];

    $conn->rollback();

    echo json_encode($output, JSON_THROW_ON_ERROR);
} catch (Throwable $error) {
    $conn->rollback();
    fwrite(STDERR, "Q020 comparison failed: " . $error->getMessage() . "\n");
    exit(1);
} finally {
    $conn->close();
}
