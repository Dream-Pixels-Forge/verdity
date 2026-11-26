# Workstream 1: Coverage Gap Closure (100% Coverage)

## Task
Achieve 100% test coverage by fixing 54 uncovered lines across key modules.

## Uncovered Lines by Module

### budget_enforcer.py (18 lines)
Lines: 15, 17, 18, 19, 20, 22, 24, 27, 28, 31
Context: Budget enforcement logic, signal handling, threshold checks

### event_queue.py (52 lines)
Lines: 11, 13, 14, 15, 17, 18, 21, 25, 26, 29
Context: Event queue operations, consume/nack/ack logic

### async_sqlite.py (9 lines)
Lines: 10, 12, 13, 14, 15, 18, 21, 22, 23
Context: Async SQLite connection, execute, commit operations

### approval_queue.py (12 lines)
Lines: 8, 10, 11, 12, 13, 14, 16, 18, 21, 22
Context: Approval queue store, enqueue, get_pending, resolve, stats

### orchestrator.py (10 lines)
Lines: 15, 17, 18, 19, 20, 21, 22, 23, 24, 26
Context: Orchestrator initialization, process_event, run checks

### router.py (10 lines)
Lines: 8, 10, 11, 12, 13, 15, 16, 17, 27, 30
Context: Routing logic, confidence computation, routing decisions

### trust_calibration.py (10 lines)
Lines: 27, 29, 30, 31, 32, 33, 35, 36, 38, 40
Context: Trust calibration, recalibration, drift detection

### metrics_store.py (10 lines)
Lines: 10, 12, 13, 15, 18, 29, 83, 84, 85, 87
Context: Metrics storage, spend recording, histogram recording

### event_queue.py (additional)
Lines: 79, 89-97, 108-110, 156-166, 175-204, 208-228

### approval_queue.py (additional)
Lines: 103-135, 139-148, 176-190, 195-200, 204-208, 211-227

## Test Strategy
For each module:
1. Identify the uncovered branch/condition
2. Write a test that exercises that specific branch
3. Use `# pragma: no cover` for truly unreachable defensive code
4. Ensure tests follow existing patterns in test_*.py files

## Success Criteria
- 100% test coverage (currently 98.5%)
- All 805+ tests passing
- No new test failures introduced