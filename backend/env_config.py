"""Load explicitly supported local settings without executing shell expressions."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {'NEBIUS_API_KEY', 'NEBIUS_MODEL', 'NEBIUS_REVIEW_MODEL',
           'GROQ_API_KEY', 'GROQ_MODEL', 'GROQ_REVIEW_MODEL', 'AI_PROVIDER_MODE',
           'ELEVENLABS_API_KEY', 'ELEVENLABS_VOICE_ID', 'CHROME_PATH',
           'LLAMACLOUD_ENABLED', 'LLAMA_CLOUD_API_KEY', 'LLAMACLOUD_ORGANIZATION_ID',
           'LLAMACLOUD_PROJECT_ID', 'LLAMACLOUD_INDEX_ID', 'LLAMACLOUD_API_GENERATION',
           'LLAMACLOUD_REGION', 'LLAMACLOUD_CONNECT_SECONDS', 'LLAMACLOUD_READ_SECONDS'}


def load_local_env(path=None):
    """Environment takes precedence. Never return or log secret values."""
    path = Path(path) if path else ROOT / '.local' / '.env'
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if key not in ALLOWED:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if value:
            os.environ.setdefault(key, value)
