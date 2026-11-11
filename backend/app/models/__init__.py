# Importing the model modules registers their tables on Base.metadata, which Alembic reads.
from app.models import ops, reference

__all__ = ["ops", "reference"]
