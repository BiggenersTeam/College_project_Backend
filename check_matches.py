from dotenv import load_dotenv; load_dotenv()
import os; from pymongo import MongoClient; from bson import ObjectId

client = MongoClient(os.getenv("MONGODB_URI"))
db = client["facetracer"]

searches = list(db["search_histories"].find({"match_found": True}).sort("search_date",-1).limit(5))
print(f"Successful matches: {len(searches)}")
for s in searches:
    mid = s.get("matched_person_id")
    print(f"  matched_person_id: {mid}")
    if mid:
        try:
            person = db["missing_persons"].find_one({"_id": ObjectId(mid)})
            if person:
                print(f"    Person: {person.get('name')} | Status: {person.get('status')}")
                print(f"    resolved field: {person.get('resolved')}")
                print(f"    resolved_at: {person.get('resolved_at')}")
            else:
                print("    NOT FOUND in missing_persons!")
        except Exception as e:
            print(f"    Error: {e}")

print()
audit = list(db["audit_logs"].find({}))
print(f"Audit logs: {len(audit)}")

print()
match_hist = list(db["match_histories"].find({}))
print(f"Match histories: {len(match_hist)}")
