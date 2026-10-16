<?php
session_start();
if (!isset($_SESSION['u'])) { header('Location: login.php'); }
if (!$ok) { echo '<meta http-equiv="refresh" content="0;url=login.php">'; exit; }
?>
<p>Hello</p>
