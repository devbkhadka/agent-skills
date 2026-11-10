#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SKILL_DIR/../.." && pwd)"

FAILED=0

reset_fixtures() {
    mkdir -p "$SKILL_DIR/tests/cases/scaffold/fixtures/invalid-transition-feature"
    cat << 'EOF' > "$SKILL_DIR/tests/cases/scaffold/fixtures/invalid-transition-feature/state.json"
{
  "schema_version": "1.0.0",
  "feature_slug": "invalid-transition-feature",
  "current_state": "STAGE_0_DOMAIN_INGESTION",
  "updated_at": "2026-08-22T10:00:00Z",
  "artifacts": {
    "stage_0_lexicon": "domain-lexicon.json",
    "stage_1_manifest": "contract-fixtures/manifest.json",
    "stage_2_scenario_matrix": "flow-simulation-matrix.json",
    "stage_3_execution_dag": "execution-dag.json",
    "stage_3_invariants": "slice-invariants.json",
    "stage_4_milestone_evidence": "milestones/evidence-summary.json",
    "stage_5_delivery_report": "delivery-report.md"
  },
  "history": [
    {
      "from_state": null,
      "to_state": "UNINITIALIZED",
      "transition_type": "INIT",
      "timestamp": "2026-08-22T10:00:00Z",
      "gate_artifact_verified": null,
      "rollback_cause": null
    }
  ]
}
EOF
    echo '{"schema_version":"1.0.0","feature_slug":"invalid-transition-feature"}' > "$SKILL_DIR/tests/cases/scaffold/fixtures/invalid-transition-feature/domain-lexicon.json"

    mkdir -p "$SKILL_DIR/tests/cases/scaffold/fixtures/missing-artifact-feature"
    cat << 'EOF' > "$SKILL_DIR/tests/cases/scaffold/fixtures/missing-artifact-feature/state.json"
{
  "schema_version": "1.0.0",
  "feature_slug": "missing-artifact-feature",
  "current_state": "STAGE_0_DOMAIN_INGESTION",
  "updated_at": "2026-08-22T10:00:00Z",
  "artifacts": {
    "stage_0_lexicon": "domain-lexicon.json",
    "stage_1_manifest": "contract-fixtures/manifest.json",
    "stage_2_scenario_matrix": "flow-simulation-matrix.json",
    "stage_3_execution_dag": "execution-dag.json",
    "stage_3_invariants": "slice-invariants.json",
    "stage_4_milestone_evidence": "milestones/evidence-summary.json",
    "stage_5_delivery_report": "delivery-report.md"
  },
  "history": [
    {
      "from_state": null,
      "to_state": "UNINITIALIZED",
      "transition_type": "INIT",
      "timestamp": "2026-08-22T10:00:00Z",
      "gate_artifact_verified": null,
      "rollback_cause": null
    }
  ]
}
EOF
    rm -f "$SKILL_DIR/tests/cases/scaffold/fixtures/missing-artifact-feature/domain-lexicon.json"

    mkdir -p "$SKILL_DIR/tests/cases/scaffold/fixtures/state-mismatch-feature"
    cat << 'EOF' > "$SKILL_DIR/tests/cases/scaffold/fixtures/state-mismatch-feature/state.json"
{
  "schema_version": "1.0.0",
  "feature_slug": "state-mismatch-feature",
  "current_state": "STAGE_0_DOMAIN_INGESTION",
  "updated_at": "2026-08-22T10:00:00Z",
  "artifacts": {
    "stage_0_lexicon": "domain-lexicon.json",
    "stage_1_manifest": "contract-fixtures/manifest.json",
    "stage_2_scenario_matrix": "flow-simulation-matrix.json",
    "stage_3_execution_dag": "execution-dag.json",
    "stage_3_invariants": "slice-invariants.json",
    "stage_4_milestone_evidence": "milestones/evidence-summary.json",
    "stage_5_delivery_report": "delivery-report.md"
  },
  "history": [
    {
      "from_state": null,
      "to_state": "UNINITIALIZED",
      "transition_type": "INIT",
      "timestamp": "2026-08-22T10:00:00Z",
      "gate_artifact_verified": null,
      "rollback_cause": null
    }
  ]
}
EOF
    echo '{"schema_version":"1.0.0","feature_slug":"state-mismatch-feature"}' > "$SKILL_DIR/tests/cases/scaffold/fixtures/state-mismatch-feature/domain-lexicon.json"

    mkdir -p "$SKILL_DIR/tests/cases/scaffold/fixtures/advance-feature"
    cat << 'EOF' > "$SKILL_DIR/tests/cases/scaffold/fixtures/advance-feature/state.json"
{
  "schema_version": "1.0.0",
  "feature_slug": "advance-feature",
  "current_state": "STAGE_0_DOMAIN_INGESTION",
  "updated_at": "2026-08-22T10:00:00Z",
  "artifacts": {
    "stage_0_lexicon": "domain-lexicon.json",
    "stage_1_manifest": "contract-fixtures/manifest.json",
    "stage_2_scenario_matrix": "flow-simulation-matrix.json",
    "stage_3_execution_dag": "execution-dag.json",
    "stage_3_invariants": "slice-invariants.json",
    "stage_4_milestone_evidence": "milestones/evidence-summary.json",
    "stage_5_delivery_report": "delivery-report.md"
  },
  "history": [
    {
      "from_state": null,
      "to_state": "UNINITIALIZED",
      "transition_type": "INIT",
      "timestamp": "2026-08-22T10:00:00Z",
      "gate_artifact_verified": null,
      "rollback_cause": null
    }
  ]
}
EOF
    echo '{"schema_version":"1.0.0","feature_slug":"advance-feature"}' > "$SKILL_DIR/tests/cases/scaffold/fixtures/advance-feature/domain-lexicon.json"
    mkdir -p "$SKILL_DIR/tests/cases/scaffold/fixtures/advance-feature/docs"
    echo '# Mock Glossary' > "$SKILL_DIR/tests/cases/scaffold/fixtures/advance-feature/docs/ubiquitous-language-glossary.md"
    echo '# Mock Entity Map' > "$SKILL_DIR/tests/cases/scaffold/fixtures/advance-feature/docs/domain-entity-map.md"


    mkdir -p "$SKILL_DIR/tests/cases/scaffold/fixtures/rollback-feature"
    cat << 'EOF' > "$SKILL_DIR/tests/cases/scaffold/fixtures/rollback-feature/state.json"
{
  "schema_version": "1.0.0",
  "feature_slug": "rollback-feature",
  "current_state": "STAGE_4_MILESTONE_EXECUTION",
  "updated_at": "2026-08-22T10:00:00Z",
  "artifacts": {
    "stage_0_lexicon": "domain-lexicon.json",
    "stage_1_manifest": "contract-fixtures/manifest.json",
    "stage_2_scenario_matrix": "flow-simulation-matrix.json",
    "stage_3_execution_dag": "execution-dag.json",
    "stage_3_invariants": "slice-invariants.json",
    "stage_4_milestone_evidence": "milestones/evidence-summary.json",
    "stage_5_delivery_report": "delivery-report.md"
  },
  "history": [
    {
      "from_state": null,
      "to_state": "UNINITIALIZED",
      "transition_type": "INIT",
      "timestamp": "2026-08-22T10:00:00Z",
      "gate_artifact_verified": null,
      "rollback_cause": null
    }
  ]
}
EOF

    mkdir -p "$SKILL_DIR/tests/cases/scaffold/fixtures/init-feature"
    cat << 'EOF' > "$SKILL_DIR/tests/cases/scaffold/fixtures/init-feature/state.json"
{
  "schema_version": "1.0.0",
  "feature_slug": "init-feature",
  "current_state": "UNINITIALIZED",
  "updated_at": "2026-08-22T10:00:00Z",
  "artifacts": {
    "stage_0_lexicon": "domain-lexicon.json",
    "stage_1_manifest": "contract-fixtures/manifest.json",
    "stage_2_scenario_matrix": "flow-simulation-matrix.json",
    "stage_3_execution_dag": "execution-dag.json",
    "stage_3_invariants": "slice-invariants.json",
    "stage_4_milestone_evidence": "milestones/evidence-summary.json",
    "stage_5_delivery_report": "delivery-report.md"
  },
  "history": [
    {
      "from_state": null,
      "to_state": "UNINITIALIZED",
      "transition_type": "INIT",
      "timestamp": "2026-08-22T10:00:00Z",
      "gate_artifact_verified": null,
      "rollback_cause": null
    }
  ]
}
EOF
}

# Always ensure fixtures are left in pristine state on exit
trap reset_fixtures EXIT

echo "=== Running Agentic Feature Planner Test Suite ==="

# 1. Reset test fixtures to pristine baseline
reset_fixtures

# 2. Run unit tests
echo "[UNIT] Running unit tests..."
if python3 -m unittest discover -s "$SKILL_DIR/tests" -p 'test_*.py' -q; then
    echo "  PASS: Unit tests passed successfully"
else
    echo "  FAIL: Unit tests failed"
    FAILED=1
fi

# 2b. Run surrogate verification fabrication red-team measurement suite
echo "[RED-TEAM] Running surrogate verification measurement suite..."
if python3 "$SKILL_DIR/tests/test_surrogate_verification.py"; then
    echo "  PASS: Surrogate verification measurement suite passed"
else
    echo "  FAIL: Surrogate verification measurement suite failed"
    FAILED=1
fi

# 3. Read stages-built.txt and execute gate checks for each stage
STAGES_FILE="$SKILL_DIR/tests/stages-built.txt"
if [ ! -f "$STAGES_FILE" ]; then
    echo "  FAIL: stages-built.txt missing at $STAGES_FILE"
    exit 1
fi

while IFS= read -r stage || [ -n "$stage" ]; do
    stage="$(echo "$stage" | tr -d '[:space:]')"
    if [ -z "$stage" ] || [[ "$stage" =~ ^# ]]; then
        continue
    fi

    echo "[GATE] Checking stage: $stage"

    # check-stdlib-only.py
    if python3 "$REPO_ROOT/plan-docs/gate-fixtures/check-stdlib-only.py" "$stage"; then
        echo "  PASS: check-stdlib-only for $stage"
    else
        echo "  FAIL: check-stdlib-only for $stage"
        FAILED=1
    fi

    # check-no-self-report.py
    if python3 "$REPO_ROOT/plan-docs/gate-fixtures/check-no-self-report.py" "$stage"; then
        echo "  PASS: check-no-self-report for $stage"
    else
        echo "  FAIL: check-no-self-report for $stage"
        FAILED=1
    fi

    # check-cases.py
    if python3 "$REPO_ROOT/plan-docs/gate-fixtures/check-cases.py" "$stage"; then
        echo "  PASS: check-cases for $stage"
    else
        echo "  FAIL: check-cases for $stage"
        FAILED=1
    fi
done < "$STAGES_FILE"

# 4. Final summary
if [ "$FAILED" -eq 0 ]; then
    echo "SUMMARY: ALL CHECKS PASSED (exit 0)"
    exit 0
else
    echo "SUMMARY: ONE OR MORE CHECKS FAILED (exit 1)"
    exit 1
fi
