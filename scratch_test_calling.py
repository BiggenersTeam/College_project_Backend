import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.services.ai_calling_service import format_to_e164, trigger_case_resolved_call

print("=== Testing E.164 Phone Formatting ===")
test_cases = [
    ("919209341992", "+919209341992"),
    ("9209341992", "+919209341992"),
    ("09209341992", "+919209341992"),
    ("+91 92093-41992", "+919209341992"),
    ("+1 (555) 000-1234", "+15550001234"),
    ("invalid", None),
]

all_passed = True
for raw, expected in test_cases:
    res = format_to_e164(raw)
    status = "PASS" if res == expected else "FAIL"
    if res != expected:
        all_passed = False
    print(f"  [{status}] Raw: '{raw}' -> Result: '{res}' (Expected: '{expected}')")

print("\n=== E.164 Test Result:", "ALL PASSED" if all_passed else "SOME FAILED", "===")

print("\n=== Testing Dry-Run Call Trigger ===")
res = trigger_case_resolved_call(
    to_phone="919209341992",
    person_name="Test Person",
    case_id="test_case_12345",
    case_status="Resolved",
    language="EN"
)
print("Call Result:", res)
