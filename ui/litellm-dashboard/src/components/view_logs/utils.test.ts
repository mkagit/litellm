import { describe, expect, it } from "vitest";
import { getAppliedGuardrailNames, getLogStatusPresentation } from "./utils";

describe("view_logs utils", () => {
  it("should derive a guardrail status presentation from status_fields", () => {
    const presentation = getLogStatusPresentation({
      status: "success",
      status_fields: {
        llm_api_status: "success",
        guardrail_status: "guardrail_intervened",
      },
      applied_guardrails: ["openai-moderation-pre"],
    });

    expect(presentation).toEqual({
      label: "Guardrail",
      tone: "warning",
      detail: "Guardrails: openai-moderation-pre",
    });
  });

  it("should fall back to guardrail_information names when applied_guardrails is absent", () => {
    expect(
      getAppliedGuardrailNames({
        guardrail_information: [
          { guardrail_name: "openai-moderation-pre" },
          { guardrail_name: "child-policy-judge-pre" },
          { guardrail_name: "openai-moderation-pre" },
        ],
      }),
    ).toEqual(["openai-moderation-pre", "child-policy-judge-pre"]);
  });
});
