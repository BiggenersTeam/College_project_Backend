"""
MongoDB document helpers.

MongoDB is schema-less — validation is handled by Pydantic schemas.
These helpers convert raw MongoDB documents (dicts with _id: ObjectId)
into plain dicts with string 'id' field, ready for Pydantic serialization.
"""
from bson import ObjectId


def doc_to_dict(document: dict) -> dict:
    """
    Convert a MongoDB document to a serializable dict.
    Transforms '_id' (ObjectId) → 'id' (str).
    """
    if document is None:
        return None
    d = dict(document)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    return d


def str_to_objectid(id_str: str) -> ObjectId:
    """
    Convert a string ID to a MongoDB ObjectId.
    Raises ValueError if the string is not a valid ObjectId.
    """
    try:
        return ObjectId(id_str)
    except Exception:
        raise ValueError(f"Invalid ObjectId: {id_str}")
