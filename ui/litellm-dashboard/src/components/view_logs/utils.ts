import { MCP_CALL_TYPES } from "./constants";

/**
 * Derive a short, human-readable display name for a log entry.
 * Strips provider prefixes, date suffixes, and version tags.
 */
export function getEventDisplayName(callType: string, model: string): string {
  const raw = (model || "").trim();
  const isMcp = MCP_CALL_TYPES.includes(callType);

  if (isMcp) {
    return raw.replace(/^mcp:\s*/i, "").split("/").pop() || raw || "mcp_tool";
  }

  const lastSegment = raw.split("/").pop() || raw;
  const noSuffix = lastSegment.replace(/-20\d{6}.*$/i, "").replace(/:.*$/, "");
  const claudeMatch = noSuffix.match(/claude-[a-z0-9-]+/i);
  if (claudeMatch) return claudeMatch[0];
  return noSuffix || "llm_call";
}

type GuardrailStatus =
  | "success"
  | "guardrail_intervened"
  | "guardrail_failed_to_respond"
  | "not_run";

export type LogStatusPresentation = {
  label: "Success" | "Failure" | "Guardrail" | "Guardrail Error";
  tone: "success" | "error" | "warning";
  detail?: string;
};

function getNormalizedGuardrailStatus(metadata?: Record<string, any>): GuardrailStatus {
  const explicitStatus = metadata?.status_fields?.guardrail_status;
  if (typeof explicitStatus === "string") {
    return explicitStatus as GuardrailStatus;
  }

  const entries = Array.isArray(metadata?.guardrail_information)
    ? metadata.guardrail_information
    : metadata?.guardrail_information
      ? [metadata.guardrail_information]
      : [];
  const rawStatus = entries.find((entry) => typeof entry?.guardrail_status === "string")?.guardrail_status;

  switch (rawStatus) {
    case "blocked":
    case "guardrail_intervened":
      return "guardrail_intervened";
    case "failure":
    case "guardrail_failed_to_respond":
      return "guardrail_failed_to_respond";
    case "success":
      return "success";
    default:
      return "not_run";
  }
}

export function getAppliedGuardrailNames(metadata?: Record<string, any>): string[] {
  const explicitNames = Array.isArray(metadata?.applied_guardrails)
    ? metadata.applied_guardrails.filter((value): value is string => typeof value === "string" && value.length > 0)
    : [];
  if (explicitNames.length > 0) {
    return Array.from(new Set(explicitNames));
  }

  const entries = Array.isArray(metadata?.guardrail_information)
    ? metadata.guardrail_information
    : metadata?.guardrail_information
      ? [metadata.guardrail_information]
      : [];
  const derivedNames = entries
    .map((entry) => entry?.guardrail_name)
    .filter((value): value is string => typeof value === "string" && value.length > 0);

  return Array.from(new Set(derivedNames));
}

export function getLogStatusPresentation(metadata?: Record<string, any>): LogStatusPresentation {
  const guardrailNames = getAppliedGuardrailNames(metadata);
  const guardrailDetail =
    guardrailNames.length > 0 ? `Guardrails: ${guardrailNames.join(", ")}` : undefined;

  if (metadata?.status === "failure") {
    return {
      label: "Failure",
      tone: "error",
      detail: guardrailDetail,
    };
  }

  const guardrailStatus = getNormalizedGuardrailStatus(metadata);
  if (guardrailStatus === "guardrail_intervened") {
    return {
      label: "Guardrail",
      tone: "warning",
      detail: guardrailDetail ?? "Guardrail modified or blocked the request.",
    };
  }

  if (guardrailStatus === "guardrail_failed_to_respond") {
    return {
      label: "Guardrail Error",
      tone: "warning",
      detail: guardrailDetail ?? "A guardrail failed to respond.",
    };
  }

  return {
    label: "Success",
    tone: "success",
    detail: guardrailDetail,
  };
}
