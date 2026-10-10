<?php
session_start();
if (!isset($_SESSION['user'])) {
    header('Location: login.php');
    exit;
}
$username = $_SESSION['user'];
echo "Welcome " . $username;
