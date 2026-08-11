import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from TA.config import BASE_DIR


DEFAULT_BASE_URL = "https://gigachat-ift.sberdevices.delta.sbrf.ru/v1"
DEFAULT_MODEL = "GigaChat-2-Pro"
DEFAULT_SCOPE = "GIGACHAT_API_CORP"
DEFAULT_TIMEOUT = 600.0


class GigaChatConfigurationError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__("GigaChat не настроен: проверьте локальные сертификаты.")


class GigaChatRequestError(RuntimeError):
    _MESSAGES = {
        "timeout": "GigaChat не ответил за отведённое время.",
        "authentication": "GigaChat отклонил данные аутентификации.",
        "access_denied": "Нет доступа к выбранной модели GigaChat.",
        "rate_limited": "GigaChat временно ограничил количество запросов.",
        "service_unavailable": "Сервис GigaChat временно недоступен.",
        "service_error": "GigaChat вернул ошибку обработки запроса.",
        "invalid_response": "GigaChat вернул ответ неизвестного формата.",
        "connection": "Не удалось установить защищённое соединение с GigaChat.",
        "unknown": "Не удалось выполнить AI-анализ.",
    }

    def __init__(self, code):
        self.code = code if code in self._MESSAGES else "unknown"
        super().__init__(self._MESSAGES[self.code])


def _positive_float(value, default):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _configured_path(base_dir, environment_name, default_relative_path):
    configured = os.environ.get(environment_name, "").strip()
    path = Path(configured) if configured else Path(default_relative_path)
    if not path.is_absolute():
        path = Path(base_dir) / path
    return path


@dataclass(frozen=True)
class GigaChatConfig:
    base_url: str
    model: str
    scope: str
    timeout: float
    ca_bundle_file: Path
    cert_file: Path
    key_file: Path
    key_file_password: Optional[str] = None

    @classmethod
    def from_env(cls, base_dir=BASE_DIR):
        base_dir = Path(base_dir)
        return cls(
            base_url=os.environ.get("GIGACHAT_BASE_URL", DEFAULT_BASE_URL).strip()
            or DEFAULT_BASE_URL,
            model=os.environ.get("GIGACHAT_MODEL", DEFAULT_MODEL).strip()
            or DEFAULT_MODEL,
            scope=os.environ.get("GIGACHAT_SCOPE", DEFAULT_SCOPE).strip()
            or DEFAULT_SCOPE,
            timeout=_positive_float(
                os.environ.get("GIGACHAT_TIMEOUT"),
                DEFAULT_TIMEOUT,
            ),
            ca_bundle_file=_configured_path(
                base_dir,
                "GIGACHAT_CA_BUNDLE_FILE",
                Path("certs") / "ca.pem",
            ),
            cert_file=_configured_path(
                base_dir,
                "GIGACHAT_CERT_FILE",
                Path("certs") / "tls.pem",
            ),
            key_file=_configured_path(
                base_dir,
                "GIGACHAT_KEY_FILE",
                Path("certs") / "tls.key",
            ),
            key_file_password=(
                os.environ.get("GIGACHAT_KEY_FILE_PASSWORD", "").strip() or None
            ),
        )

    def validate_certificates(self):
        certificate_paths = (
            self.ca_bundle_file,
            self.cert_file,
            self.key_file,
        )
        if any(not path.is_file() for path in certificate_paths):
            raise GigaChatConfigurationError("certificates_missing")


def _default_client_factory(**kwargs):
    from gigachat import GigaChat

    return GigaChat(**kwargs)


def _exception_code(error):
    error_name = type(error).__name__
    if isinstance(error, TimeoutError) or error_name in {
        "TimeoutException",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
    }:
        return "timeout"
    return {
        "AuthenticationError": "authentication",
        "ForbiddenError": "access_denied",
        "RateLimitError": "rate_limited",
        "ServerError": "service_unavailable",
        "GigaChatException": "service_error",
        "ConnectError": "connection",
        "ConnectionError": "connection",
        "ReadError": "connection",
        "WriteError": "connection",
        "NetworkError": "connection",
        "SSLError": "connection",
    }.get(error_name, "unknown")


class GigaChatHelper:
    def __init__(
        self,
        config=None,
        client_factory: Optional[Callable[..., object]] = None,
    ):
        self.config = config or GigaChatConfig.from_env()
        self.client_factory = client_factory or _default_client_factory

    def _client_parameters(self):
        # Параметры соответствуют проверенной корпоративной интеграции GigaChat.
        # max_retries не передаём: gigachat==0.1.42.post2 его не поддерживает.
        return {
            "base_url": self.config.base_url,
            "ca_bundle_file": str(self.config.ca_bundle_file),
            "cert_file": str(self.config.cert_file),
            "key_file": str(self.config.key_file),
            "key_file_password": self.config.key_file_password,
            "model": self.config.model,
            "scope": self.config.scope,
            "timeout": self.config.timeout,
            "verify_ssl_certs": True,
        }

    def generate(self, prompt):
        self.config.validate_certificates()
        client = None
        try:
            client = self.client_factory(**self._client_parameters())
            response = client.chat(prompt)
            content = response.choices[0].message.content
            if not isinstance(content, str) or not content.strip():
                raise GigaChatRequestError("invalid_response")
            return content
        except GigaChatRequestError:
            raise
        except Exception as error:
            raise GigaChatRequestError(_exception_code(error)) from error
        finally:
            if client is not None:
                close = getattr(client, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
