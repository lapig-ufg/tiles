"""
MongoDB CRUD para embedding jobs e artifacts.
Collection: embedding_jobs
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.mongodb import get_database


# --------------------------------------------------------------------------- #
# Collection accessor                                                          #
# --------------------------------------------------------------------------- #

async def get_jobs_collection():
    db = get_database()
    return db.embedding_jobs


# --------------------------------------------------------------------------- #
# Job CRUD                                                                     #
# --------------------------------------------------------------------------- #

async def create_job(job: Dict[str, Any]) -> str:
    """Insere job (upsert por _id para idempotencia). Retorna job_id."""
    coll = await get_jobs_collection()
    job.setdefault("created_at", datetime.utcnow())
    job.setdefault("updated_at", datetime.utcnow())
    await coll.update_one(
        {"_id": job["_id"]},
        {"$setOnInsert": job},
        upsert=True,
    )
    return job["_id"]


async def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    coll = await get_jobs_collection()
    return await coll.find_one({"_id": job_id})


async def update_job_status(job_id: str, status: str, **fields) -> None:
    coll = await get_jobs_collection()
    update: Dict[str, Any] = {"status": status, "updated_at": datetime.utcnow()}
    update.update(fields)
    await coll.update_one({"_id": job_id}, {"$set": update})


async def update_job_fields(job_id: str, **fields) -> None:
    coll = await get_jobs_collection()
    fields["updated_at"] = datetime.utcnow()
    await coll.update_one({"_id": job_id}, {"$set": fields})


async def list_jobs(
    limit: int = 20,
    offset: int = 0,
    status: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    coll = await get_jobs_collection()
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    total = await coll.count_documents(query)
    cursor = coll.find(query).sort("created_at", -1).skip(offset).limit(limit)
    items = await cursor.to_list(length=limit)
    return items, total


async def delete_job(job_id: str) -> bool:
    coll = await get_jobs_collection()
    result = await coll.delete_one({"_id": job_id})
    return result.deleted_count > 0


# --------------------------------------------------------------------------- #
# Artifacts                                                                    #
# --------------------------------------------------------------------------- #

async def add_artifact(job_id: str, artifact: Dict[str, Any]) -> None:
    coll = await get_jobs_collection()
    await coll.update_one(
        {"_id": job_id},
        {"$push": {"artifacts": artifact}, "$set": {"updated_at": datetime.utcnow()}},
    )


async def get_artifacts(job_id: str) -> List[Dict[str, Any]]:
    job = await get_job(job_id)
    if job is None:
        return []
    return job.get("artifacts", [])
