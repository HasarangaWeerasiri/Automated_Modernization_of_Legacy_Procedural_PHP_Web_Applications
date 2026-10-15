<?php
switch ($_GET['m']) {
  case 'a': $sql = "SELECT id FROM users"; break;
  default: $sql = "SELECT id FROM teams";
}
mysqli_query($conn, $sql);
