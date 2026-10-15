<?php
require_once __DIR__ . '/db.php';
// H03 static JOIN
$sql = "SELECT a.id, a.full_name, m.team_name FROM agents a INNER JOIN teams m ON m.id = a.team_id ORDER BY a.id";
$result = mysqli_query($conn, $sql);
page_header('Agents');
echo '<table><tr><th>ID</th><th>Agent</th><th>Team</th></tr>';
while ($row = mysqli_fetch_assoc($result)) echo '<tr><td>'.h($row['id']).'</td><td>'.h($row['full_name']).'</td><td>'.h($row['team_name']).'</td></tr>';
echo '</table>'; page_footer();
