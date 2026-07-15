# Qwen/Bailian Connection Check

- checked_at: `2026-07-14T10:39:28.856964+00:00`
- status: `live_connection_ok`
- provider: `Alibaba Cloud Model Studio / Qwen`
- model: `qwen3.7-max-2026-06-08`
- endpoint_scope: `shared_cn_beijing`
- endpoint_display: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- endpoint_sha256: `5891aed827c4e67b2d7c0c73ea819327ce3f2b6ef72213cd79669486a26b1ead`
- mode: `qwen_openai_compatible`
- enabled: `True`
- api_key_present: `True`
- credential_policy: API keys are read from environment variables and never persisted in run artifacts.

## Response Without Secrets

```json
{
  "verdict": "pass",
  "qwen_role": "State that Qwen is used only for language critique, not numeric gates.",
  "safety_boundary": "No secrets or credentials included."
}
```

## Safety Boundary

This proof records only credential-free metadata. API keys, account IDs, and access tokens are never written to disk.
