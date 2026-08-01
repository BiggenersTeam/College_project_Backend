"""
FaceTracer Configuration Check Script
Run: python check_config.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

print("\n" + "="*55)
print("  FaceTracer Configuration Check")
print("="*55)

checks = [
    ("MONGODB_URI",          os.getenv("MONGODB_URI",""),         "MongoDB connection string"),
    ("SECRET_KEY",           os.getenv("SECRET_KEY",""),          "JWT secret key"),
    ("GOOGLE_CLIENT_ID",     os.getenv("GOOGLE_CLIENT_ID",""),    "Google OAuth Client ID"),
    ("GOOGLE_CLIENT_SECRET", os.getenv("GOOGLE_CLIENT_SECRET",""),"Google OAuth Client Secret"),
    ("TWILIO_ACCOUNT_SID",   os.getenv("TWILIO_ACCOUNT_SID",""),  "Twilio Account SID"),
    ("TWILIO_AUTH_TOKEN",    os.getenv("TWILIO_AUTH_TOKEN",""),   "Twilio Auth Token"),
    ("TWILIO_PHONE_NUMBER",  os.getenv("TWILIO_PHONE_NUMBER",""), "Twilio Phone Number"),
    ("VAPI_API_KEY",         os.getenv("VAPI_API_KEY",""),        "Vapi API Key"),
    ("VAPI_ASSISTANT_ID",    os.getenv("VAPI_ASSISTANT_ID",""),   "Vapi Assistant ID"),
]

all_ok = True
for key, val, desc in checks:
    if val and val.strip() and not val.startswith("your-"):
        print(f"  [OK] {key}")
    else:
        # Voice providers can be optional if either Twilio or Vapi is configured
        if "VAPI" in key or "TWILIO" in key:
            print(f"  [--] {key}  (Optional/Provider Specific)")
        else:
            print(f"  [!!] {key}  <-- MISSING")
            all_ok = False

# Test Twilio connection if credentials set
twilio_sid   = os.getenv("TWILIO_ACCOUNT_SID","")
twilio_token = os.getenv("TWILIO_AUTH_TOKEN","")
if twilio_sid and twilio_token and not twilio_sid.startswith("your-"):
    print()
    print("  Testing Twilio connection...")
    try:
        from twilio.rest import Client
        client = Client(twilio_sid, twilio_token)
        account = client.api.accounts(twilio_sid).fetch()
        print(f"  [OK] Twilio Account: {account.friendly_name} ({account.status})")
    except ImportError:
        print("  [!!] twilio not installed. Run: pip install twilio")
    except Exception as e:
        print(f"  [!!] Twilio connection failed: {e}")

# Test Vapi credentials if set
vapi_key = os.getenv("VAPI_API_KEY", "")
vapi_assistant = os.getenv("VAPI_ASSISTANT_ID", "")
if vapi_key and vapi_assistant and not vapi_key.startswith("your-"):
    print()
    print("  Testing Vapi connection...")
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://api.vapi.ai/assistant/{vapi_assistant}",
            headers={"Authorization": f"Bearer {vapi_key}"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print(f"  [OK] Vapi Assistant connection verified!")
    except Exception as e:
        print(f"  [!!] Vapi connection check: {e}")

print()
if all_ok:
    print("  All configurations set!")
else:
    print("  Some values are missing — see above.")
print("="*55 + "\n")
