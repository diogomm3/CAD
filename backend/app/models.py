from pydantic import BaseModel, Field


class DownloadableFile(BaseModel):
    name: str
    extension: str
    category: str
    url: str | None = None
    downloadable: bool = False
    reason: str | None = None
    original_name: str | None = None
    local_path: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    source_url: str | None = None


class ModelResult(BaseModel):
    id: str
    source: str
    title: str
    author: str | None = None
    model_url: str
    thumbnail_url: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    downloads: int | None = None
    likes: int | None = None
    favorites: int | None = None
    rating: float | None = None
    popularity_value: float | None = None
    popularity_label: str | None = None
    available_files: list[DownloadableFile] = Field(default_factory=list)
    description: str | None = None
    license: str | None = None
    published_at: str | None = None
    raw_metadata: dict | None = None
