# Importing the model modules registers their tables on Base.metadata, which Alembic reads.
from app.models import macro, ops, reference

__all__ = ["macro", "ops", "reference"]
