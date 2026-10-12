// Response types for this page, from Component 1's contract (docs/generation-spec.md section 8).
// Fields not in the contract do not appear: a missing field is a C2Todo, never row.<field>.

export type AppointmentStats = {
  total: number;
  revenue: number;
};
