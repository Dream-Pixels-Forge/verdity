---
spec: coverage-requirements
version: 1.0.0
title: Coverage Requirements Specification
status: active

requirements:
  # Global coverage thresholds
  global:
    minimum_coverage: 95
    target_coverage: 100
    authenticity_threshold: 95

  # Per-module minimum coverage
  modules:
    src/verdity/enforcement.py: 100
    src/verdity/github_client.py: 100
    src/verdity/mcp_server.py: 100
    src/verdity/approval_queue.py: 100
    src/verdity/verification_gate.py: 99
    src/verdity/worker.py: 90
    src/verdity/event_queue.py: 95
    src/verdity/gateway/app.py: 99
    src/verdity/platforms/bitbucket.py: 98
    src/verdity/platforms/gitlab.py: 98

  # Authenticity requirements
  authenticity:
    minimum_score: 0.95
    fake_coverage_tolerance: 0
    mock_test_ratio_max: 0.3

  # Silent failure requirements
  silent_failures:
    allowed_critical: 0
    allowed_high: 0
    allowed_warnings: 10

  # Fake implementation tolerance
  fake_implementations:
    allowed_critical: 0
    allowed_high: 0
    allowed_medium: 5
    allowed_low: 10

  # Test quality metrics
  test_quality:
    integration_test_min_ratio: 0.2
    e2e_test_min_ratio: 0.1
    mock_test_max_ratio: 0.3

  # CI/CD Gates
  gates:
    pre_merge:
      - coverage_threshold: 95
      - authenticity_score: 0.95
      - critical_silent_failures: 0
      - critical_fakes: 0

    pre_release:
      - coverage_threshold: 100
      - authenticity_score: 0.98
      - critical_silent_failures: 0
      - critical_fakes: 0
      - high_fakes: 0

  # Exemptions (with justification required)
  exemptions:
    - file: "src/verdity/worker.py"
      lines: "79, 89-97, 108-110, 180-184"
      reason: "Async cancellation handling - requires integration test infrastructure"
      expires: "2026-12-31"

    - file: "src/verdity/event_queue.py"
      lines: "232-243"
      reason: "Not-connected error path - requires integration test"
      expires: "2026-12-31"

    - file: "src/verdity/gateway/app.py"
      lines: "112"
      reason: "Default allowlist behavior - intentional design decision"
      expires: "2026-12-31"

    - file: "src/verdity/platforms/bitbucket.py"
      lines: "165-166"
      reason: "Missing secret config warning - edge case"
      expires: "2026-12-31"

    - file: "src/verdity/platforms/gitlab.py"
      lines: "155-156"
      reason: "Missing secret config warning - edge case"
      expires: "2026-12-31"

    - file: "src/verdity/verification_gate.py"
      lines: "116"
      reason: "Escalation scheduled flag - rare edge case"
      expires: "2026-12-31"

    - file: "src/verdity/platforms/bitbucket.py"
      lines: "165-166"
      reason: "Signature verification edge case"
      expires: "2026-12-31"

    - file: "src/verdity/platforms/gitlab.py"
      lines: "155-156"
      reason: "Signature verification edge case"
      expires: "2026-12-31"
