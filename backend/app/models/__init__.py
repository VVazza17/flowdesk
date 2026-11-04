# Importing ops registers its tables on Base.metadata, which Alembic reads.
from app.models import ops

__all__ = ["ops"]
