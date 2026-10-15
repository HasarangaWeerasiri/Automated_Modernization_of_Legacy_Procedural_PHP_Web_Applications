<?php
require_once __DIR__ . '/db.php';
// H01 static multi-table JOIN and ORDER BY
$sql = "SELECT t.id, t.subject, t.priority, t.status, a.full_name, m.team_name FROM tickets t JOIN agents a ON a.id = t.agent_id JOIN teams m ON m.id = a.team_id ORDER BY t.id DESC";
$result = mysqli_query($conn, $sql);
page_header('Ticket Queue');
echo '<p><a class="button" href="create.php">+ Create ticket</a> <a href="search.php">Search tickets</a></p>';
echo '<table><tr><th>ID</th><th>Subject</th><th>Priority</th><th>Status</th><th>Agent</th><th>Team</th><th>Actions</th></tr>';
while ($row = mysqli_fetch_assoc($result)) {
  echo '<tr><td>'.h($row['id']).'</td><td>'.h($row['subject']).'</td><td>'.h($row['priority']).'</td><td>'.h($row['status']).'</td><td>'.h($row['full_name']).'</td><td>'.h($row['team_name']).'</td><td><a href="view.php?id='.urlencode($row['id']).'">View</a> · <a href="edit.php?id='.urlencode($row['id']).'">Edit</a></td></tr>';
}
echo '</table>'; page_footer();
