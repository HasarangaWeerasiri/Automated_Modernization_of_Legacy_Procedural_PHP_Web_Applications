import type { AppointmentStats } from "../../lib/api-types";
import { C2Todo, unresolvedCondition } from "../../lib/c2-todo";

export default function Page() {
  return (
    <div className="container-fluid" style={{ marginTop: "50px" }}>
      <h3 style={{ marginLeft: "40%", paddingBottom: "20px", fontFamily: "'IBM Plex Sans', sans-serif" }}>
        {" Welcome \u00a0"}
        <C2Todo id="f003#00034" reason="needs_auth_context" />
      </h3>
      <C2Todo id="f003#00041" reason="cuts_across_business_logic" />
      {unresolvedCondition("f003#00141", "needs_auth_context") && (
        <a className="nav-link" href="admin-panel1.php">Receptionist panel</a>
      )}
    </div>
  );
}

function Abstained_f003_00041({ data }: { data: AppointmentStats }) {
  return (
    <>
      {unresolvedCondition("f003#00041", "needs_auth_context") ? (
        <div className="admin-stats">
          <p>
            {"Active appointments: "}
            {data.total}
          </p>
          <p>
            {"Fees collected: \u20b9"}
            {data.revenue}
          </p>
          <p>
            {"Signed in as "}
            <C2Todo id="f003#00118" reason="needs_auth_context" />
          </p>
        </div>
      ) : (
        <p className="text-muted">Statistics are visible to administrators only.</p>
      )}
    </>
  );
}
