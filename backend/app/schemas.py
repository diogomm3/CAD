from pydantic import BaseModel, Field, field_validator
from .models import ModelResult

class SearchRequest(BaseModel):
    query: str
    @field_validator("query")
    @classmethod
    def valid_query(cls, value):
        value = value.strip()
        if not value or len(value) > 120: raise ValueError("Query must be between 1 and 120 characters")
        return value

class Selection(BaseModel):
    source: str
    model_id: str
    model_url: str

class ProjectRequest(BaseModel):
    query: str
    models: list[Selection] = Field(min_length=1, max_length=5)

class ProjectJob(BaseModel):
    job_id: str
    status: str
    progress: int
    message: str
    projects: list[dict] = Field(default_factory=list)

