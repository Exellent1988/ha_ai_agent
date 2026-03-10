"""Constants for the AI Agent HA integration."""

DOMAIN = "ai_agent_ha"
CONF_API_KEY = "api_key"
CONF_WEATHER_ENTITY = "weather_entity"

# AI Provider configuration keys
CONF_LLAMA_TOKEN = "llama_token"  # nosec B105
CONF_OPENAI_TOKEN = "openai_token"  # nosec B105
CONF_GEMINI_TOKEN = "gemini_token"  # nosec B105
CONF_OPENROUTER_TOKEN = "openrouter_token"  # nosec B105
CONF_ANTHROPIC_TOKEN = "anthropic_token"  # nosec B105
CONF_ALTER_TOKEN = "alter_token"  # nosec B105
CONF_ZAI_TOKEN = "zai_token"  # nosec B105
CONF_LOCAL_URL = "local_url"
CONF_LOCAL_MODEL = "local_model"
CONF_OLLAMA_URL = "ollama_url"

# Available AI providers
AI_PROVIDERS = [
    "llama",
    "openai",
    "gemini",
    "openrouter",
    "anthropic",
    "alter",
    "zai",
    "local",
    "ollama",
]

# AI Provider constants
CONF_MODELS = "models"
CONF_SYSTEM_PROMPT = "system_prompt"
CONF_LANGUAGE = "language"
CONF_REQUEST_TIMEOUT = "request_timeout"

# Default values for model configuration
DEFAULT_LANGUAGE = "Deutsch"
# Request timeout in seconds (for AI API calls; local models may need higher values)
DEFAULT_REQUEST_TIMEOUT = 300

# Max total prompt size in characters (Ollama truncates at 65536; keep under to avoid truncation)
MAX_PROMPT_CHARS = 60000

# Supported AI providers
DEFAULT_AI_PROVIDER = "openai"
