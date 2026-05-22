from pydantic import BaseModel


class SettingItem(BaseModel):
    key: str
    value: str | None
    is_secret: bool

    model_config = {"from_attributes": True}


class SettingsResponse(BaseModel):
    settings: list[SettingItem]


class SettingUpdate(BaseModel):
    value: str | None
