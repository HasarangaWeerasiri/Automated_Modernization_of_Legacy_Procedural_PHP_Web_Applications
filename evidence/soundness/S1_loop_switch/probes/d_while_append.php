<?php
$sql = "DELETE FROM users WHERE 1=0";
$i = 0;
while ($i < 3) { $sql .= " OR id = $i"; $i++; }
mysqli_query($conn, $sql);
