import json
import os
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = PACKAGE_ROOT / "models"
ENV_PATH = PACKAGE_ROOT / ".env"
PREFIX_ROOT = Path(sys.prefix)
DATA_ROOT = Path(sysconfig.get_paths().get("data", sys.prefix)).resolve()
PREFIX_CANDIDATES = (
    PREFIX_ROOT,
    PREFIX_ROOT.resolve(),
    DATA_ROOT,
)


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
    env_candidates = [Path.cwd() / ".env"]
    env_candidates.extend(prefix_root / ".env" for prefix_root in PREFIX_CANDIDATES)
    env_candidates.append(ENV_PATH)

    seen = set()
    for env_path in env_candidates:
        env_key = str(env_path)
        if env_key in seen:
            continue
        seen.add(env_key)
        try:
            env_text = env_path.read_text(encoding="utf-8")
        except OSError:
            continue

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


def load_model_config(model_name=None, model_file=None):
    if model_file:
        path = Path(model_file).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Model config file not found: {path}")
        if model_name is None:
            model_name = path.stem
    else:
        candidate_paths = [Path.cwd() / "models" / f"{model_name}.json"]
        candidate_paths.extend(
            prefix_root / "models" / f"{model_name}.json" for prefix_root in PREFIX_CANDIDATES
        )
        candidate_paths.append(MODEL_DIR / f"{model_name}.json")

        seen = set()
        deduped_paths = []
        for candidate_path in candidate_paths:
            path_key = str(candidate_path)
            if path_key in seen:
                continue
            seen.add(path_key)
            deduped_paths.append(candidate_path)
        path = next((candidate for candidate in deduped_paths if candidate.exists()), None)
        if path is None:
            raise FileNotFoundError(f"Model config not found for {model_name} in models/")

    with path.open("r", encoding="utf-8") as handle:
        raw_config = json.load(handle)

    providers = raw_config.get("provider", {})
    selected_model = find_model_config(providers, model_name, path)
    provider_id, provider_config, resolved_model_name, model_config = selected_model
    base_url = provider_config.get("options", {}).get("baseURL")
    if not base_url:
        raise ValueError(f"Model config {path} is missing provider.options.baseURL")

    return ModelConfig(
        config_name=resolved_model_name,
        model_id=model_config.get("model", resolved_model_name),
        display_name=model_config.get("name", resolved_model_name),
        provider_id=provider_id,
        provider_name=provider_config.get("name", provider_id),
        base_url=base_url.rstrip("/"),
        tools_enabled=bool(model_config.get("tools", False)),
        api_key_env=provider_config.get("options", {}).get("apiKeyEnv"),
        extra_body=dict(model_config.get("extra_body", {})),
    )


def find_model_config(providers, model_name, path):
    for provider_id, provider_config in providers.items():
        models = provider_config.get("models", {})
        if model_name not in models:
            continue

        return provider_id, provider_config, model_name, models[model_name]

    all_models = []
    for provider_id, provider_config in providers.items():
        for fallback_model_name, model_config in provider_config.get("models", {}).items():
            all_models.append((provider_id, provider_config, fallback_model_name, model_config))

    if len(all_models) == 1:
        return all_models[0]

    raise ValueError(f"Model {model_name} was not found inside {path}")
