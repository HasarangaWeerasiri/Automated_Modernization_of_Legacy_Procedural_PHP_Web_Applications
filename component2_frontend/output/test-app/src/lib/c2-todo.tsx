// Component 2, Stage 5 (docs/generation-spec.md section 1).
// C2Todo marks a part the generator could not produce faithfully. It renders nothing;
// every use is listed, with its reason, in output/stage5_<mock>.json.
export function C2Todo(props: { id: string; reason: string }): null {
  void props;
  return null;
}

// A condition the generator could not translate (section 6). Always false.
export function unresolvedCondition(nodeId: string, reason: string): boolean {
  void nodeId;
  void reason;
  return false;
}
