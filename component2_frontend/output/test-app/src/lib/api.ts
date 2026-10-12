import type { AppointmentStats } from "./api-types";

// TODO(C2): token forwarding is Component 1's design (docs/generation-spec.md section 8).
function authHeaders(): Record<string, string> {
  return {};
}

export async function getApiAdminAppointmentStats(): Promise<AppointmentStats> {
  const response = await fetch(`${process.env.API_BASE_URL}/api/admin/appointment-stats`, {
    cache: "no-store",
    headers: authHeaders(),
  });
  return response.json();
}
