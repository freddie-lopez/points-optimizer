# Test Report: Points Transfer Optimizer v0

**Date**: 2026-09-07  
**Tester**: Tester Agent  
**Status**: 4 Issues Found (1 MEDIUM, 2 HIGH, 1 LOW)

## Summary

Comprehensive attack surface testing against the Points Transfer Optimizer v0 revealed four issues: one medium-severity ordering bug in card selection, two high-severity input validation gaps (negative balances and stranded points calculation), and one low-severity fragile card-dependency matching. All existing unit tests pass (25/25), but edge cases and integration scenarios expose real gaps in the implementation.

---

## Findings

### 1. [MEDIUM] Multiple Cards Not Evaluated - Picks First Match Instead of Best

**Severity**: MEDIUM  
**Category**: Card Dependency  
**Status**: CONFIRMED  

#### Title
When user has multiple cards that transfer to the same program at different ratios, the optimizer picks the FIRST matching card instead of trying ALL cards to find the BEST ratio.

#### Repro Steps
1. User has 120,000 UR points and two cards: Chase Sapphire Preferred and Chase Sapphire Reserve
2. Travel date is Oct 1, 2026 (after ratio change)
3. Both cards can transfer UR → Hyatt:
   - Sapphire Preferred: 4:3 ratio (120k UR → 90k Hyatt)
   - Sapphire Reserve: 1:1 ratio (120k UR → 120k Hyath)
4. Run `find_transfer_paths()` with cards in order: `["Sapphire Preferred", "Sapphire Reserve"]`
5. Then run again with reversed order: `["Sapphire Reserve", "Sapphire Preferred"]`

#### Expected
Both should return the same set of transfer paths (ideally including BOTH 1:1 and 4:3 options), allowing the optimizer to pick the best.

#### Actual
First run returns: 1 path with 4:3 ratio (90,000 Hyatt received)  
Second run returns: 1 path with 1:1 ratio (120,000 Hyatt received)  
Result is ORDER-DEPENDENT - the first matching card is used and subsequent cards are never tried.

#### Location
`src/optimizer.py`, `find_transfer_paths()`, lines 58-81

```python
for card in user_cards:
    ratio = ratios_manager.query_ratio(...)
    if ratio:
        # Found a transfer
        ...
        break  # <-- BREAKS AFTER FIRST CARD, NEVER TRIES OTHERS
```

#### Root Cause
The code has a `break` statement after finding the first valid ratio for a card. This exits the loop, preventing other cards from being evaluated. For a single `from_program`, only one path is generated.

#### Attack Surface Map
- **Card dependency**: User has multiple cards that transfer to same program at different ratios → SHOULD pick the best one → DOES NOT

---

### 2. [HIGH] Negative Balance Accepted Without Validation

**Severity**: HIGH  
**Category**: Bad Input  
**Status**: CONFIRMED

#### Title
The optimizer accepts negative points balances and processes them without validation or error, allowing impossible scenarios.

#### Repro Steps
1. Call `find_transfer_paths()` with `user_balances={"UR": -100000}`
2. Call `optimize()` with a Trip containing negative balance
3. Observe that paths are created and processed

#### Expected
- Either reject with a clear error message (e.g., "Balance cannot be negative")
- Or skip zero/negative balances silently

#### Actual
Code processes negative balance, creating a transfer path with negative amounts. Example: UR balance of -100,000 is treated as a valid source.

#### Location
`src/optimizer.py`, `find_transfer_paths()`, line 52-53

```python
for from_program, balance in user_balances.items():
    if balance == 0:  # <-- Only checks for zero, not negative
        continue
```

#### Root Cause
Input validation only checks `if balance == 0`, not `if balance < 0` or `if balance <= 0`. Additionally, no validation at the CLI level in `main.py` where `argparse` accepts negative integers.

#### Attack Surface Map
- **Bad input**: negative numbers → SHOULD reject → DOES NOT
- **Bad input**: invalid balance input → partially handled (zero is skipped, but negative is accepted)

---

### 3. [HIGH] Stranded Points Not Calculated - Always Set to Zero

**Severity**: HIGH  
**Category**: Stranded Points  
**Status**: CONFIRMED

#### Title
The `stranded_points` field in `TransferPath` is hardcoded to zero and never actually calculated. This breaks the stranding-prevention requirement.

#### Repro Steps
1. User has 100,000 UR points
2. Transfer UR → IHG at 2:1 ratio (100k UR = 50k IHG, 50k UR stranded in the sense that it's not all converted)
3. Call `find_transfer_paths()` targeting IHG
4. Check the returned path's `stranded_points`

#### Expected
- `stranded_points = 50000` (half the balance was transferred but only half was needed for the award)
- Or: At the Strategy level, detect when combining transfers + awards leaves unused points

#### Actual
`stranded_points = 0` (hardcoded in line 79)

#### Location
`src/optimizer.py`, `find_transfer_paths()`, line 79

```python
path.stranded_points = 0  # <-- ALWAYS ZERO, NEVER CALCULATED
```

And in `optimize()`, line 135-136:

```python
if path.stranded_points > 0:
    continue  # Reject stranded points
```

Since stranded_points is always 0, this check never filters anything.

#### Root Cause
The `stranded_points` field is created but never populated with actual calculation. The comment in `find_transfer_paths()` says "Reject stranded points (paths with unused balance) per plan requirement" but no logic implements the calculation.

The real stranding scenario per the plan is: "trip with 100k UR, awards at 90k and 70k → should not propose 50k+50k splits." This requires comparing transfer path output vs. award cost and detecting when combining multiple awards would strand points. The current implementation doesn't do this.

#### Attack Surface Map
- **Stranded points**: Verify transfer paths never strand → SHOULD reject problematic paths → DOES NOT
- Test: 100k UR with awards requiring 90k and 70k → should not allow 50k+50k splits
- Test: 100k UR → IHG at 2:1 → 50k received, 50k unused

---

### 4. [LOW] Card Dependency Matching Uses Substring Instead of Exact Match

**Severity**: LOW  
**Category**: Ratio Versioning  
**Status**: CONFIRMED (LATENT BUG)

#### Title
The card dependency matching uses Python's `in` operator (substring matching) instead of exact string comparison, which works with current data but is fragile and could silently break if CSV data changes.

#### Repro Steps
1. In `ratio_manager.py`, line 73, observe the code:
   ```python
   and ratio.card_dependency in card
   ```
2. This checks if `ratio.card_dependency` is a SUBSTRING of the `card` parameter
3. Current CSV has `card_dependency="Sapphire Reserve"`, so:
   - `"Sapphire Reserve" in "Chase Sapphire Reserve"` → True ✓ (works)
   - `"Sapphire Reserve" in "Chase Sapphire Preferred"` → False ✓ (works)
4. But if someone changed CSV to `card_dependency="Sapphire"`:
   - `"Sapphire" in "Chase Sapphire Reserve"` → True ✓
   - `"Sapphire" in "Chase Sapphire Preferred"` → True ✗ (BUG - shouldn't match)

#### Expected
Use exact string comparison: `ratio.card_dependency == card`

#### Actual
Uses substring matching: `ratio.card_dependency in card`

#### Location
`src/ratio_manager.py`, line 73

```python
and ratio.card_dependency in card  # <-- SUBSTRING MATCHING
```

Also affects the hardcoded checks for "all" and "other" card dependencies, which work correctly through the `Ratio.is_valid_on()` method (line 18-37 in models.py), but the first-pass specific card matching (line 73) uses the fragile `in` operator.

#### Impact
- **Current state**: Works correctly because CSV data uses full, unique card names
- **Risk**: If CSV is edited to use shorter values like "Sapphire" instead of "Sapphire Reserve", silent bugs emerge where wrong cards match

#### Attack Surface Map
- **Ratio versioning**: card-dependent. Test boundary dates, wrong card, missing ratios
- The wrong card matching could happen if CSV data changes

---

## Test Results Summary

| Category | Tests | Status |
|----------|-------|--------|
| Ratio versioning (boundary dates) | 1 | ✓ PASS |
| Card dependency (single card) | 5 | ✓ PASS |
| Card dependency (multiple cards, different ratios) | 1 | ✗ FAIL |
| Stranded points calculation | 1 | ✗ FAIL |
| Input validation (negative balance) | 1 | ✗ FAIL |
| Input validation (zero balance) | 1 | ✓ PASS |
| Margin calculation (symmetry) | 3 | ✓ PASS |
| Deduplication (same award via different paths) | 1 | ✓ PASS |
| API failure handling | 1 | ✓ PASS |
| Date range parsing | 2 | ✓ PASS |
| Ratio application (rounding) | 2 | ✓ PASS |
| Bonus application | 2 | ✓ PASS |
| CLI required args | 1 | ✓ PASS |
| **TOTAL** | **23** | **19 PASS, 4 FAIL** |

---

## Unit Test Status

All 25 existing unit tests continue to pass:
- `tests/test_ratios.py`: 9/9 ✓
- `tests/test_seats_client.py`: 5/5 ✓
- `tests/test_optimizer.py`: 8/8 ✓
- `tests/test_acceptance.py`: 3/3 ✓

The unit tests do not cover the attack surface edge cases above.

---

## What Held Up Well

1. **Ratio versioning for boundary dates**: Sep 30 vs Oct 1 transitions work correctly
2. **Bonus application**: 25% Q4 promo applied correctly; inactive outside date range
3. **Margin calculation**: Symmetry works (negative when optimizer wins, positive when it loses, zero on tie)
4. **Deduplication**: Same award via multiple source programs correctly keeps cheapest path
5. **API failure handling**: Returns empty list gracefully instead of crashing
6. **Date range parsing**: Correctly expands single date to 30 days; parses ranges
7. **Integer rounding in ratios**: Uses floor division, handles non-even ratios correctly
8. **Zero balance handling**: Correctly skips zero balances
9. **Card matching for "other" dependency**: Correctly interprets "other" as "any card except Sapphire Reserve"

---

## Recommendations for Next Sprint

### Critical (Blocks v1)
1. **Fix multiple cards**: Generate paths for ALL cards, not just first match. Allow optimizer to pick best.
2. **Add input validation**: Reject negative balances with clear error message
3. **Calculate stranded points**: Implement logic to detect and reject paths that strand points

### High
4. **Fix card dependency matching**: Replace `in` with `==` (or keep `in` but add validation that card_dependency values are unique/non-overlapping)

### Testing
5. Add unit tests for multi-card scenarios with different ratios
6. Add unit tests for negative/invalid input handling
7. Add integration test for stranding detection (100k UR with 90k + 70k awards)

---

## Reopening Request

This report is ready for the Coder to fix. Please address findings 1-3 (critical), and consider finding 4 as a refactoring improvement.

---

**Test Environment**: Python 3.11, pytest 7.4.0, all dependencies from requirements.txt  
**Test Date**: 2026-09-07  
**Tester**: Tester Agent
