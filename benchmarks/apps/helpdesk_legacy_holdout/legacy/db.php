<?php
/* Local research fixture only. Never use these unparameterized pages in production. */
$host = getenv('HOLDOUT_DB_HOST') ?: '127.0.0.1';
$user = getenv('HOLDOUT_DB_USER') ?: 'root';
$pass = getenv('HOLDOUT_DB_PASSWORD') ?: '';
$name = getenv('HOLDOUT_DB_NAME') ?: 'c3_helpdesk_holdout';
$conn = mysqli_connect($host, $user, $pass, $name);
if (!$conn) { http_response_code(500); exit('Database connection failed'); }
mysqli_set_charset($conn, 'utf8mb4');
function h($value) { return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8'); }
function page_header($title) {
    echo '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'.h($title).'</title><link rel="stylesheet" href="style.css"></head><body><header><h1>Community Service Desk</h1><nav><a href="index.php">Tickets</a><a href="create.php">New Ticket</a><a href="agents.php">Agents</a><a href="report.php">Team Report</a></nav></header><main><h2>'.h($title).'</h2>';
}
function page_footer() { echo '</main><footer>Research fixture • procedural PHP / mysqli</footer></body></html>'; }
