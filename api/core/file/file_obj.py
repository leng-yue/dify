from collections.abc import Sequence
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from core.model_runtime.entities.message_entities import ImagePromptMessageContent
from extensions.ext_database import db

from .tool_file_parser import ToolFileParser
from .upload_file_parser import UploadFileParser


class FileTransferMethod(str, Enum):
    REMOTE_URL = "remote_url"
    LOCAL_FILE = "local_file"
    TOOL_FILE = "tool_file"

    @staticmethod
    def value_of(value):
        for member in FileTransferMethod:
            if member.value == value:
                return member
        raise ValueError(f"No matching enum found for value '{value}'")


class FileType(str, Enum):
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"
    CUSTOM = "custom"

    @staticmethod
    def value_of(value):
        for member in FileType:
            if member.value == value:
                return member
        raise ValueError(f"No matching enum found for value '{value}'")


class ImageConfig(BaseModel):
    """
    NOTE: This part of validation is deprecated, but still used in app features "Image Upload".
    """

    number_limits: int = 0
    transfer_methods: Sequence[FileTransferMethod] = Field(default_factory=list)
    detail: ImagePromptMessageContent.DETAIL | None = None


class FileExtraConfig(BaseModel):
    """
    File Upload Entity.
    """

    image_config: Optional[ImageConfig] = None
    allowed_file_types: Sequence[FileType] = Field(default_factory=list)
    allowed_extensions: Sequence[str] = Field(default_factory=list)
    allowed_upload_methods: Sequence[FileTransferMethod] = Field(default_factory=list)
    number_limits: int = 0


class FileBelongsTo(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"

    @staticmethod
    def value_of(value):
        for member in FileBelongsTo:
            if member.value == value:
                return member
        raise ValueError(f"No matching enum found for value '{value}'")


class File(BaseModel):
    id: Optional[str] = None  # message file id
    tenant_id: str
    type: FileType
    transfer_method: FileTransferMethod
    url: Optional[str] = None  # remote url
    related_id: Optional[str] = None
    extra_config: Optional[FileExtraConfig] = None
    filename: Optional[str] = None
    extension: Optional[str] = None
    mime_type: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "__variant": self.__class__.__name__,
            "tenant_id": self.tenant_id,
            "type": self.type.value,
            "transfer_method": self.transfer_method.value,
            "url": self.preview_url,
            "remote_url": self.url,
            "related_id": self.related_id,
            "filename": self.filename,
            "extension": self.extension,
            "mime_type": self.mime_type,
        }

    @property
    def markdown(self) -> str:
        preview_url = self.preview_url
        if self.type == FileType.IMAGE:
            text = f'![{self.filename or ""}]({preview_url})'
        else:
            text = f"[{self.filename or preview_url}]({preview_url})"

        return text

    @property
    def data(self) -> Optional[str]:
        """
        Get image data, file signed url or base64 data
        depending on config MULTIMODAL_SEND_IMAGE_FORMAT
        """
        return self._preview_url()

    @property
    def preview_url(self) -> Optional[str]:
        return self._preview_url(force_url=True)

    @property
    def prompt_message_content(self):
        if self.type == FileType.IMAGE:
            if self.extra_config is None:
                return None

            image_config = self.extra_config.image_config

            if self.data is None:
                return None

            return ImagePromptMessageContent(
                data=self.data,
                detail=image_config.detail
                if image_config and image_config.detail
                else ImagePromptMessageContent.DETAIL.LOW,
            )

    def _preview_url(self, force_url: bool = False) -> str | None:
        if self.type == FileType.IMAGE:
            if self.transfer_method == FileTransferMethod.REMOTE_URL:
                return self.url
            elif self.transfer_method == FileTransferMethod.LOCAL_FILE:
                from models import UploadFile

                upload_file = (
                    db.session.query(UploadFile)
                    .filter(UploadFile.id == self.related_id, UploadFile.tenant_id == self.tenant_id)
                    .first()
                )

                return UploadFileParser.get_image_data(upload_file=upload_file, force_url=force_url)
            elif self.transfer_method == FileTransferMethod.TOOL_FILE:
                # add sign url
                assert self.related_id is not None
                assert self.extension is not None
                return ToolFileParser.get_tool_file_manager().sign_file(
                    tool_file_id=self.related_id, extension=self.extension
                )

        return None

    @model_validator(mode="after")
    def validate_after(self):
        match self.transfer_method:
            case FileTransferMethod.REMOTE_URL:
                if not self.url:
                    raise ValueError("Missing file url")
                if not isinstance(self.url, str) or not self.url.startswith("http"):
                    raise ValueError("Invalid file url")
            case FileTransferMethod.LOCAL_FILE:
                if not self.related_id:
                    raise ValueError("Missing file related_id")
            case FileTransferMethod.TOOL_FILE:
                if not self.related_id:
                    raise ValueError("Missing file related_id")

        # Validate the extra config.
        if not self.extra_config:
            return self

        if self.extra_config.allowed_file_types:
            if self.type not in self.extra_config.allowed_file_types and self.type != FileType.CUSTOM:
                raise ValueError(f"Invalid file type: {self.type}")

        if self.extra_config.allowed_extensions and self.extension not in self.extra_config.allowed_extensions:
            raise ValueError(f"Invalid file extension: {self.extension}")

        if (
            self.extra_config.allowed_upload_methods
            and self.transfer_method not in self.extra_config.allowed_upload_methods
        ):
            raise ValueError(f"Invalid transfer method: {self.transfer_method}")

        match self.type:
            case FileType.IMAGE:
                # NOTE: This part of validation is deprecated, but still used in app features "Image Upload".
                if not self.extra_config.image_config:
                    return self
                # TODO: skip check if transfer_methods is empty, because many test cases are not setting this field
                if (
                    self.extra_config.image_config.transfer_methods
                    and self.transfer_method not in self.extra_config.image_config.transfer_methods
                ):
                    raise ValueError(f"Invalid transfer method: {self.transfer_method}")

        return self
