<?php
require_once __DIR__ . '/db.php';
// H09 aggregate JOIN/GROUP BY
$sql = "SELECT m.team_name, COUNT(t.id) AS ticket_count FROM teams m LEFT JOIN agents a ON a.team_id = m.id LEFT JOIN tickets t ON t.agent_id = a.id GROUP BY m.id, m.team_name ORDER BY m.id";
$result = mysqli_query($conn, $sql);
page_header('Tickets by Team');
echo '<table><tr><th>Team</th><th>Tickets</th></tr>';
while ($row = mysqli_fetch_assoc($result)) echo '<tr><td>'.h($row['team_name']).'</td><td>'.h($row['ticket_count']).'</td></tr>';
echo '</table>'; page_footer();
