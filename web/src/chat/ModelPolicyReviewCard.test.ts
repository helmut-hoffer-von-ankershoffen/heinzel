import { expect, it } from "vitest";
import { supportedPolicy } from "@/chat/ModelPolicyReviewCard";

it("refuses changed provider, credential binding or any tool grant", () => {
  const model = "openai:gpt-4.1-mini-2025-04-14";
  const valid = {
    access: "pydantic-ai",
    model,
    binding: {
      owner: `u-${"a".repeat(32)}`,
      access: "pydantic-ai",
      model,
      endpoint: "https://api.openai.com/v1",
      credential_fingerprint: "b".repeat(64),
    },
    allowed_tools: {},
    allowed_skill_tools: [],
  };
  expect(supportedPolicy(valid)).toBe(true);
  expect(supportedPolicy({ ...valid, access: "claude-code" })).toBe(false);
  expect(supportedPolicy({ ...valid, allowed_tools: { "plugin:read": "c".repeat(64) } })).toBe(false);
  expect(supportedPolicy({ ...valid, allowed_skill_tools: ["skill.propose_draft"] })).toBe(false);
  expect(supportedPolicy({ ...valid, binding: { ...valid.binding, credential_fingerprint: "short" } })).toBe(false);
});
