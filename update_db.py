import logging
from core.database import SessionLocal, ModelEndpoint

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db = SessionLocal()
try:
    endpoints = db.query(ModelEndpoint).all()
    updated = 0
    for ep in endpoints:
        logger.info(f"Checking endpoint: id={ep.id}, name={ep.name}, url={ep.base_url}, supports_tools={ep.supports_tools}")
        # Match by name or base url (local endpoints)
        is_local = (
            any(h in (ep.base_url or "") for h in ("localhost", "127.0.0.1", "host.docker.internal"))
            or "qwen" in (ep.name or "").lower()
        )
        if is_local:
            ep.supports_tools = False
            updated += 1
            logger.info(f"-> Set supports_tools = False for: {ep.name} ({ep.base_url})")
    
    if updated > 0:
        db.commit()
        logger.info(f"Successfully committed database changes for {updated} endpoint(s).")
    else:
        logger.warning("No matching endpoints found to update.")
finally:
    db.close()
