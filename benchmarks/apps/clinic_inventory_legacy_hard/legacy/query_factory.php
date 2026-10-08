<?php
function makeDoctorQuery($departmentId) {
    return "SELECT id, full_name FROM doctors WHERE department_id = " . $departmentId;
}
