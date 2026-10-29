"""Stable, secret-free errors shared by model requests and desktop diagnostics."""

from __future__ import annotations

ERRORS = {
    "TLS_HANDSHAKE": (
        "HTTPS 加密连接在握手期间中断。",
        "检查当前网络连接方式；可在连接设置中测试代理或直连。",
        True,
    ),
    "PROXY_CONNECT": (
        "无法连接到当前代理。",
        "检查代理是否运行，或在连接设置中选择直连后测试。",
        True,
    ),
    "TIMEOUT": ("模型服务连接或响应超时。", "检查网络后重试。", True),
    "AUTH": (
        "模型服务拒绝了身份验证或访问权限。",
        "检查 API Key、服务区域及模型权限。",
        False,
    ),
    "QUOTA": ("模型服务额度不足或请求受限。", "检查服务账户额度和请求限额。", False),
    "MODEL_UNAVAILABLE": (
        "所选模型或服务地址不可用。",
        "检查模型名称、服务地址及账户开通情况。",
        False,
    ),
    "CONNECTION": ("与模型服务的连接失败。", "使用连接测试检查当前网络路径。", True),
    "UNKNOWN": ("模型请求失败。", "查看后台日志以进一步定位问题。", False),
}


def diagnose_provider_error(exc: BaseException, route: str = "env") -> dict:
    chain = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    names = {type(item).__name__ for item in chain}
    statuses = {getattr(item, "status_code", None) for item in chain}
    # Exception text is used only for classification, never returned to the UI.
    text = " ".join(str(item) for item in chain).lower()
    if (
        statuses & {402, 429}
        or names & {"RateLimitError"}
        or any(
            marker in text
            for marker in (
                "insufficient_quota",
                "insufficient_balance",
                "arrearage",
                "quota_exceeded",
                "credit balance",
            )
        )
    ):
        code = "QUOTA"
    elif statuses & {401, 403} or names & {
        "AuthenticationError",
        "PermissionDeniedError",
    }:
        code = "AUTH"
    elif statuses & {404} or "model_not_found" in text:
        code = "MODEL_UNAVAILABLE"
    elif names & {"ProxyError"}:
        code = "PROXY_CONNECT"
    elif (
        names & {"SSLError", "SSLEOFError", "SSLCertVerificationError"}
        or "[ssl:" in text
    ):
        code = "TLS_HANDSHAKE"
    elif any("Timeout" in name for name in names):
        code = "TIMEOUT"
    elif names & {
        "APIConnectionError",
        "ConnectError",
        "ReadError",
        "RemoteProtocolError",
        "ConnectionResetError",
    }:
        code = "CONNECTION"
    else:
        code = "UNKNOWN"
    message, suggestion, retryable = ERRORS[code]
    return {
        "code": code,
        "message": message,
        "suggestion": suggestion,
        "retryable": retryable,
        "route": route if route in {"env", "direct"} else "env",
    }


def annotate_provider_error(exc: Exception, route: str) -> Exception:
    """Preserve exception type/cause for existing recovery and SDK consumers."""
    info = diagnose_provider_error(exc, route)
    if info["code"] == "UNKNOWN" and not hasattr(exc, "status_code"):
        return exc
    message = f"[JW_PROVIDER:{info['code']}] {info['message']} {info['suggestion']} (route={info['route']})"
    exc.args = (message,)
    if hasattr(exc, "message"):
        exc.message = message
    return exc
