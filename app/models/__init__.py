"""Import every model here so `Base.metadata` is complete for Alembic autogenerate."""
from app.models.job import Job, RenderOutput
from app.models.project import Analysis, Project, ProjectEdit
from app.models.sfx import SfxAsset
from app.models.usage import UsageEvent
from app.models.user import User

__all__ = [
    "User",
    "Project",
    "Analysis",
    "ProjectEdit",
    "Job",
    "RenderOutput",
    "SfxAsset",
    "UsageEvent",
]
