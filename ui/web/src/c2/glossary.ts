// Short names of Component 2's boundary rules, from docs/php-analysis/boundary-rules.md (the rule tables).
// A rule or reason that is not listed here is shown as it is, never explained by a guess.

export const RULES: Record<string, string> = {
  "R-L1": "Loop with output → List + Item",
  "R-L2a": "List container: nearest open tag",
  "R-L2b": "List with no wrapper (Fragment)",
  "R-L3": "One-element loop → inline .map()",
  "R-L3b": "Text-only loop → inline .map()",
  "R-L4": "Nested loops",
  "R-L5": "Loop with no output: not a boundary",
  "R-L6": "Unmatched closing tags flagged",
  "R-L7": "Chunked loop: abstain",
  "R-I1": "Page guard",
  "R-I2": "List guard → empty state",
  "R-I2b": "Multi-loop list guard",
  "R-I3": "Attribute conditional → JSX expression",
  "R-I3b": "Attribute-presence conditional",
  "R-I4": "Small content if (≤ 3 elements) → inline conditional",
  "R-I5": "Fallback → own Conditional component",
  "R-I6": "Sibling chain → one conditional value",
  "R-I7": "No output left: not a boundary",
  "R-I8": "Exit-terminated arm",
  "R-I8c": "Exit arm in an included file or function",
  "R-F1": "Function component",
  "R-F1b": "Caller-opened container",
  "R-F2": "Shell pair (header + footer)",
};

export const REASONS: Record<string, string> = {
  unlabelled:
    "No concern label: the C2 test harness gives none (Member 01's core would), so Stage 2 marks the node 'review'.",
  exclusivity_not_verified: "R-I6 chain: the conditions' mutual exclusivity is not verified, so it is 'review'.",
  loop_body: "The body of an output loop (R-L1 Item).",
  function_component: "R-F1: a user function with output, called from a page.",
  small_content_if: "R-I4: a content if with at most 3 elements.",
  page: "The page itself.",
  no_endpoint: "No endpoint in the contract for this data: the data wiring is Component 1's (C2Todo).",
};

export const SEVERITY_ORDER = ["todo", "review", "info"] as const;
