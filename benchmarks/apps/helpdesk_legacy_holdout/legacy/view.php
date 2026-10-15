<?php
require_once __DIR__ . '/db.php';
$id = $_GET['id'] ?? '1';
// H02 input-derived scalar filter
$sql = "SELECT id, subject, description, priority, status, agent_id FROM tickets WHERE id = $id";
$result = mysqli_query($conn, $sql);
$row = mysqli_fetch_assoc($result);
page_header('Ticket Details');
if (!$row) { echo '<p>Ticket not found.</p>'; }
else {
  echo '<p><b>Ticket #'.h($row['id']).'</b></p><p>'.h($row['subject']).'</p><p>'.nl2br(h($row['description'])).'</p><p>Priority: '.h($row['priority']).' · Status: '.h($row['status']).'</p>';
  echo '<a href="edit.php?id='.urlencode($row['id']).'">Edit ticket</a>';
  echo '<form action="delete.php" method="post"><input type="hidden" name="id" value="'.h($row['id']).'"><button class="danger">Delete</button></form>';
}
page_footer();
