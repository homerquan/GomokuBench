import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


@dataclass(frozen=True)
class ModelConfig:
    config_name: str
    model_id: str
    display_name: str
    provider_id: str
    provider_name: str
    base_url: str
    tools_enabled: bool
    api_key_env: Optional[str]
    extra_body: Dict[str, object]

    def get_api_key(self):
        if not self.api_key_env:
            return None

        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            api_key = load_dotenv_value(self.api_key_env)
        if not api_key:
            raise ValueError(
                f"Model {self.config_name} requires environment variable {self.api_key_env}."
            )
        return api_key


def load_dotenv_value(key):
    try:
        env_text = ENV_PATH.read_text(encoding="utf-8")
    except OSError:
        return None

    prefix = f"{key}="
    for raw_line in env_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        value = line[len(prefix):].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        return value

    return None


def load_model_config(model_name):
    path = MODEL_DIR / f"{model_name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Model config not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        raw_config = json.load(handle)

    providers = raw_config.get("provider", {})
    for provider_id, provider_config in providers.items():
        models = provider_config.get("models", {})
        if model_name not in models:
            continue

        model_config = models[model_name]
        base_url = provider_config.get("options", {}).get("baseURL")
        if not base_url:
            raise ValueError(f"Model config {path} is missing provider.options.baseURL")

        return ModelConfig(
            config_name=model_name,
            model_id=model_config.get("model", model_name),
            display_name=model_config.get("name", model_name),
            provider_id=provider_id,
            provider_name=provider_config.get("name", provider_id),
            base_url=base_url.rstrip("/"),
            tools_enabled=bool(model_config.get("tools", False)),
            api_key_env=provider_config.get("options", {}).get("apiKeyEnv"),
            extra_body=dict(model_config.get("extra_body", {})),
        )

    raise ValueError(f"Model {model_name} was not found inside {path}")
