# Qwen/Bailian Connection Check

- checked_at: `2026-07-14T11:54:38.451267+00:00`
- status: `dry_run_fallback`
- provider: `Alibaba Cloud Model Studio / Qwen`
- model: `qwen3.7-max-2026-06-08`
- endpoint_scope: `shared_cn_beijing`
- endpoint_display: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- endpoint_sha256: `5891aed827c4e67b2d7c0c73ea819327ce3f2b6ef72213cd79669486a26b1ead`
- mode: `deterministic_fallback`
- enabled: `False`
- api_key_present: `False`
- credential_policy: API keys are read from environment variables and never persisted in run artifacts.

## Response Without Secrets

```json
{
  "verdict": "fallback_not_live",
  "qwen_role": "language-only critique layer",
  "safety_boundary": "numeric solar-cycle results stay controlled by deterministic code"
}
```

## Safety Boundary

This proof records only credential-free metadata. Dedicated endpoint hosts and workspace IDs are masked; only their endpoint scope and SHA-256 fingerprint are persisted. Shared public endpoints may be recorded verbatim. API keys, account IDs, and access tokens are never written to disk.
