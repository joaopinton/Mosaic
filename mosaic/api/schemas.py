"""Corpi delle richieste, validati da pydantic ai confini dell'API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Uids = list[str]


class PhotoPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    caption: str | None = Field(default=None, max_length=2000)
    favorite: bool | None = None
    private: bool | None = None
    keywords_add: list[str] = Field(default_factory=list, max_length=50)
    keywords_remove: list[str] = Field(default_factory=list, max_length=50)
    labels_add: list[str] = Field(default_factory=list, max_length=50)
    labels_remove: list[str] = Field(default_factory=list, max_length=50)


class BatchAction(BaseModel):
    uids: Uids = Field(min_length=1, max_length=20000)
    action: Literal["favorite", "unfavorite", "private", "public", "archive", "restore", "add_to_album", "remove_from_album"]
    album: str | None = None


class AlbumCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    uids: Uids = Field(default_factory=list, max_length=20000)
    source: str | None = Field(default=None, max_length=64)  # album da cui nasce (una selezione salvata, D-27)


class AlbumPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    favorite: bool | None = None
    private: bool | None = None
    cover: str | None = None


class CurateRequest(BaseModel):
    count: int = Field(default=24, ge=1, le=200)
    shape: str = Field(default="", max_length=12, pattern=r"^(|landscape|portrait|square|\d{1,2}(\.\d{1,2})?:\d{1,2}(\.\d{1,2})?)$")
    text: str = Field(default="", max_length=300)
    mode: Literal["quality", "balanced", "variety"] = "balanced"
    people: bool = True
    videos: bool = False
    seed: int = Field(default=0, ge=0, le=1_000_000)


class PosterText(BaseModel):
    label: str = Field(default="", max_length=30)
    value: str = Field(default="", max_length=80)


class PosterRequest(BaseModel):
    uids: Uids = Field(min_length=1, max_length=200)
    paper: Literal["a4", "a3", "a2", "30x40", "50x70", "70x100", "square"] = "a3"
    landscape: bool = False
    style: Literal["mondrian", "gallery", "none"] = "mondrian"
    line: Literal["thin", "medium", "bold"] = "medium"
    colors: int = Field(default=3, ge=0, le=8)
    seed: int = Field(default=1, ge=0, le=1_000_000)
    dpi: int = Field(default=200, ge=72, le=300)
    title: str = Field(default="", max_length=120)
    occasion: str = Field(default="", max_length=80)
    date: str = Field(default="", max_length=80)
    place: str = Field(default="", max_length=80)
    texts: list[PosterText] | None = Field(default=None, max_length=8)  # scritte dell'utente (D-33); None = automatiche
    font: Literal["bahnschrift", "georgia", "impact", "consolas"] = "bahnschrift"
    case: Literal["auto", "upper", "lower"] = "auto"
    album: str = Field(default="", max_length=64)  # album di riferimento: il poster si salva lì (D-27)


class EnhanceOptions(BaseModel):
    light: bool = True
    contrast: bool = True
    vivid: bool = True
    harmonize: bool = True
    strength: Literal["light", "medium", "strong"] = "medium"


class CropFormat(BaseModel):
    """Formato del ritaglio (D-34): un rapporto w:h o, con `pixels`, la misura esatta delle copie."""
    w: int = Field(ge=1, le=20000)
    h: int = Field(ge=1, le=20000)
    pixels: bool = False

    @model_validator(mode="after")
    def _not_too_thin(self) -> CropFormat:
        if max(self.w / self.h, self.h / self.w) > 10:
            raise ValueError("proporzioni troppo estreme: al massimo 10:1")
        return self


class ExportRequest(BaseModel):
    uids: Uids = Field(min_length=1, max_length=20000)
    dest: str = Field(default="", max_length=500)   # cartella; con `zip` non serve (si scarica dal browser)
    zip: bool = False
    zip_name: str = Field(default="Foto Mosaic", max_length=120)
    raw: bool = False
    videos: bool = True
    enhance: EnhanceOptions | None = None
    crop: CropFormat | None = None
    fmt: Literal["original", "jpeg", "png", "webp"] = "original"
    quality: int = Field(default=92, ge=40, le=100)
    max_side: int | None = Field(default=None, ge=64, le=20000)
    numbered: bool = False

    @model_validator(mode="after")
    def _needs_destination(self) -> ExportRequest:
        if not self.zip and not self.dest.strip():
            raise ValueError("scegli una cartella o l'opzione zip")
        return self


class ExportPreview(BaseModel):
    uids: Uids = Field(min_length=1, max_length=5000)
    enhance: EnhanceOptions = Field(default_factory=EnhanceOptions)


class ExportCropPreview(BaseModel):
    uids: Uids = Field(min_length=1, max_length=5000)
    crop: CropFormat


class FolderPick(BaseModel):
    start: str = Field(default="", max_length=500)


class DropPaths(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)


class DropAdd(DropPaths):
    folders: Literal["link", "copy"] = "link"


class UidList(BaseModel):
    uids: Uids = Field(min_length=1, max_length=5000)


class Name(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SubjectPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    hidden: bool | None = None
    favorite: bool | None = None


class SubjectMerge(BaseModel):
    keep: str
    drop: str


class IndexRequest(BaseModel):
    rescan: bool = False


class ImportRequest(BaseModel):
    source: str = Field(min_length=1, max_length=1000)
    move: bool = False


class CleanupRequest(BaseModel):
    purge: bool = False


class RenameRequest(BaseModel):
    pattern: str | None = Field(default=None, max_length=200)
    force: bool = False
    uids: Uids | None = None


class RollbackRequest(BaseModel):
    batch_id: str | None = None
    all: bool = False


class CaptionRequest(BaseModel):
    uids: Uids | None = None
    force: bool = False


class CloudKey(BaseModel):
    provider: str
    key: str = Field(default="", max_length=512)


class RemovePhotos(BaseModel):
    uids: Uids = Field(min_length=1, max_length=20000)


class UnlinkRoot(BaseModel):
    root: str = Field(min_length=1, max_length=1000)


class RestoreExcluded(BaseModel):
    sha1s: list[str] | None = Field(default=None, max_length=5000)


class ConfigPatch(BaseModel):
    changes: dict
